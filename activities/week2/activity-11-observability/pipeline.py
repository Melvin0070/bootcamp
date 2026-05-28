"""
FossilRAG ingestion pipeline — observability-fixed version (Activity 11).

Same business logic as `broken/pipeline.py` (the Activity 10 ingestion
pipeline, run as a Lambda on S3 ObjectCreated). What changed:

1. Every stage (read / normalise / write / upload) is wrapped in
   ``stage_timer`` which emits one CloudWatch EMF record with
   ``Latency`` and ``Errors``. Failure rate and p99 latency become
   real metrics, alarmable from the CloudFormation template in
   ``infra/observability.yaml``.
2. The ``RowsIngested`` and ``RowsDropped`` counts are emitted as
   metrics, so a sudden drop in row count alarms even if no exception
   was raised. (That's the failure mode the broken baseline missed:
   the pipeline ran "successfully" but produced an empty parquet.)
3. A ``request_id`` correlation key is propagated through every log
   line and EMF record so an on-call can pivot from one record in
   Logs Insights to the full invocation history.
4. Exceptions are re-raised. The Lambda invocation marks as errored,
   the built-in ``Errors`` metric ticks, AND our per-stage ``Errors``
   metric ticks — so the dashboard tells you which stage broke
   without grepping logs.
5. The S3 client is module-level so it survives Lambda warm starts
   (one of the things we couldn't measure before).

Logs are emitted at two levels deliberately:
   - EMF JSON records  → drive metrics and the dashboard
   - structured ``key=value`` log lines → human-readable in Logs
     Insights without parsing a JSON column

Both go to stdout, both end up in the same log group, both are
queryable.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from pathlib import Path

import boto3
import pandas as pd

from metrics import emit, stage_timer

logger = logging.getLogger("fossilrag.pipeline")

REQUIRED_COLUMNS = ("species", "age_mya")

_S3 = None


def _s3():
    """Module-level boto3 client so warm Lambdas don't re-init the connection pool."""
    global _S3
    if _S3 is None:
        _S3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return _S3


# ---------------------------------------------------------------------------
# Pure transform (unchanged from Activity 10 — kept testable)
# ---------------------------------------------------------------------------


def normalise(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Rename, lower-case, strip, drop-NA. Returns (df, rows_dropped)."""
    rename_map = {"Species": "species", "Age_MYA": "age_mya"}
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    before = len(df)
    df = df.copy()
    df = df.dropna(subset=list(REQUIRED_COLUMNS))
    df["species"] = df["species"].astype(str).str.strip().str.lower()
    df = df[df["species"] != ""]
    after = len(df)
    return df.reset_index(drop=True), before - after


# ---------------------------------------------------------------------------
# Stages — each one is a thin function wrapped in a stage_timer at the
# call site. Splitting them up is what makes per-stage metrics possible.
# ---------------------------------------------------------------------------


def read_csv_from_s3(bucket: str, key: str) -> pd.DataFrame:
    obj = _s3().get_object(Bucket=bucket, Key=key)
    return pd.read_csv(obj["Body"])


def write_parquet(df: pd.DataFrame, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    return out


def upload(local_path: str | Path, bucket: str, key: str) -> None:
    _s3().upload_file(str(local_path), bucket, key)


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def handler(event, context):
    """S3-triggered Lambda. Exceptions propagate; metrics fire either way."""
    request_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    logger.info("event=invocation_start request_id=%s", request_id)

    bucket = event["Records"][0]["s3"]["bucket"]["name"]
    key = event["Records"][0]["s3"]["object"]["key"]
    out_key = key.rsplit(".", 1)[0] + ".parquet"

    try:
        with stage_timer("read", request_id=request_id):
            df = read_csv_from_s3(bucket, key)

        with stage_timer("normalise", request_id=request_id):
            df, dropped = normalise(df)

        with stage_timer("write", request_id=request_id):
            local = write_parquet(df, Path("/tmp") / Path(out_key).name)

        with stage_timer("upload", request_id=request_id):
            upload(local, bucket, out_key)

        # Row-count gauges — these are how we detect "ran successfully
        # but produced empty output," which was the silent failure
        # mode in broken/pipeline.py.
        emit(
            {
                "RowsIngested": (float(len(df)), "Count"),
                "RowsDropped": (float(dropped), "Count"),
                "Invocations": (1.0, "Count"),
            },
            dimensions={"Stage": "summary"},
            properties={"event": "pipeline_done", "request_id": request_id},
        )
        logger.info(
            "event=pipeline_done request_id=%s rows_ingested=%d rows_dropped=%d",
            request_id,
            len(df),
            dropped,
        )
        return {"statusCode": 200, "rows": len(df), "request_id": request_id}

    except Exception:
        # Top-level failure metric — separate from per-stage Errors so
        # the dashboard can distinguish "stage 3 failed" from "the whole
        # invocation failed before any stage ran" (e.g. malformed event).
        # Emit Invocations=1 here too: it's the denominator for any
        # failure-rate calculation, so the "Invocations vs failures"
        # panel would undercount total invocations if failures were
        # missing from the Invocations series.
        emit(
            {
                "Invocations": (1.0, "Count"),
                "InvocationFailures": (1.0, "Count"),
            },
            dimensions={"Stage": "summary"},
            properties={"event": "pipeline_failed", "request_id": request_id},
        )
        logger.exception("event=pipeline_failed request_id=%s", request_id)
        raise


# ---------------------------------------------------------------------------
# CLI (for local + integration testing — not used in Lambda)
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="FossilRAG ingestion pipeline (observability)")
    p.add_argument("--bucket", required=True)
    p.add_argument("--key", required=True)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    event = {"Records": [{"s3": {"bucket": {"name": args.bucket}, "object": {"key": args.key}}}]}
    try:
        handler(event, None)
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
