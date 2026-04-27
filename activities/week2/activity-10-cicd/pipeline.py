"""
FossilRAG ingestion pipeline — fixed version for Activity 10.

Same business logic as `broken/pipeline.py` (normalise the species/age
columns and write a parquet to S3) but factored so it is testable in
isolation and runnable both locally and inside CI.

The file is deliberately small. Activity 10 is about *automation*, not
about rewriting the pipeline; the goal is to make it possible for CI
to assert that the transform is correct on every PR, and for the
deploy job to push the validated file to staging on merge.

Public surface
--------------
- `normalise(df)`   — pure pandas transform; the unit-test target.
- `read_csv(path)`  — thin wrapper, separated so tests can stub I/O.
- `write_parquet(df, path)` — same.
- `upload(local, bucket, key)` — boto3 wrapper; integration test target.
- `run(...)`        — orchestrator used by the entrypoint.

Logging
-------
Structured `logging` records (logger name = "fossilrag.pipeline") so a
CloudWatch subscription filter can route on `event=...` keys. No
`print()` calls — they were the original sin in `broken/pipeline.py`.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import boto3
import pandas as pd

logger = logging.getLogger("fossilrag.pipeline")

REQUIRED_COLUMNS = ("species", "age_mya")


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Rename, lower-case, strip, drop-NA. Pure function — no I/O.

    Kept deliberately small so a single pytest run covers every branch.
    """
    rename_map = {"Species": "species", "Age_MYA": "age_mya"}
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    df = df.copy()
    # dropna BEFORE astype(str). On Python 3.11/12 with stable pandas,
    # `None.astype(str)` returns the literal string "None", which then
    # survives dropna and lands in the output as "none". Drop first.
    df = df.dropna(subset=list(REQUIRED_COLUMNS))
    df["species"] = df["species"].astype(str).str.strip().str.lower()
    df = df[df["species"] != ""]
    return df.reset_index(drop=True)


def read_csv(path: str | Path) -> pd.DataFrame:
    logger.info("event=read_csv path=%s", path)
    return pd.read_csv(path)


def write_parquet(df: pd.DataFrame, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    logger.info("event=write_parquet path=%s rows=%d", out, len(df))
    return out


def upload(local_path: str | Path, bucket: str, key: str, *, client=None) -> None:
    s3 = client or boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    s3.upload_file(str(local_path), bucket, key)
    logger.info("event=upload bucket=%s key=%s", bucket, key)


def run(input_csv: str, output_bucket: str, output_key: str, *, s3_client=None) -> Path:
    df = read_csv(input_csv)
    df = normalise(df)
    local = write_parquet(df, Path("/tmp") / Path(output_key).name)
    upload(local, output_bucket, output_key, client=s3_client)
    return local


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="FossilRAG ingestion pipeline")
    p.add_argument("--input", required=True, help="path to input CSV")
    p.add_argument("--bucket", required=True, help="output S3 bucket")
    p.add_argument("--key", required=True, help="output S3 key (must end in .parquet)")
    args = p.parse_args(argv)
    if not args.key.endswith(".parquet"):
        p.error("--key must end in .parquet")
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        run(args.input, args.bucket, args.key)
    except Exception:
        logger.exception("event=pipeline_failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
