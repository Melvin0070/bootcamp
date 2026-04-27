"""
FossilRAG ingestion pipeline — BROKEN baseline.

This file is the "before" state for Activity 10. The code itself runs —
it ingests a CSV of fossil records, normalises columns, and writes a
parquet file to S3 — but the project around it has **zero automation**:

Anti-patterns deliberately preserved
------------------------------------
1. No tests at all. The author tested locally once, decided it worked,
   and shipped. The next bug to land in main will be discovered by the
   downstream team that consumes the parquet.
2. No CI. Pull requests merge with a thumbs-up emoji. A typo in a column
   rename has shipped to staging twice in the last quarter.
3. Manual deploy. The runbook says "ssh into the bastion, scp the .py
   file, restart the cron." The single SRE who knew the bastion's IP is
   on parental leave.
4. No branch protection on `main`. Anyone with write access can push
   directly. Two contributors have force-pushed over each other's work
   in the last month.
5. No dependency pinning beyond a hand-edited list at the top of this
   file. `pip install pandas pyarrow boto3` resolves differently on
   every laptop.
6. No linting, no formatting, no type checking. Mixed tabs/spaces and
   inconsistent quotes mean every diff is half-cosmetic.
7. `print()` everywhere instead of structured logs. CloudWatch parses
   nothing; on-call grep through plain text on a 2 AM page.
8. `if __name__ == "__main__"` block reads paths from positional argv
   with no validation. A wrong arg silently writes to the wrong bucket.
9. No idempotency. Re-running the pipeline on the same day creates a
   duplicate parquet partition; downstream dedupe is somebody else's
   problem.

The fix in `pipeline.py` (one level up) keeps the same business logic
but adds the infrastructure that should have been there from day one:
GitHub Actions CI, branch protection, OIDC-based deploy to staging on
merge, structured logs, dependency pinning, and tests.
"""

# Anti-pattern: dependencies are an unpinned hand-edited list at the top
# of the entrypoint file. CI would have caught the version drift.
#   pip install pandas pyarrow boto3

import sys

import boto3
import pandas as pd


def run(input_csv, output_bucket, output_key):
    # Anti-pattern: print() instead of structured logs. The pipeline
    # logs land as plain text in CloudWatch; nothing is queryable.
    print(f"reading {input_csv}")
    df = pd.read_csv(input_csv)

    # Business logic (the bit that actually matters and we DO want to
    # keep). Normalises the schema produced by the ingest team.
    df = df.rename(columns={"Species": "species", "Age_MYA": "age_mya"})
    df["species"] = df["species"].str.strip().str.lower()
    df = df.dropna(subset=["species", "age_mya"])

    # Anti-pattern: write parquet to a deterministic S3 key with no
    # idempotency check. Re-running the pipeline silently overwrites
    # whatever was there, and a partial write leaves a corrupt object
    # with no way to detect it.
    out_path = f"/tmp/{output_key.split('/')[-1]}"
    df.to_parquet(out_path, index=False)
    print(f"wrote local {out_path}")

    s3 = boto3.client("s3")
    s3.upload_file(out_path, output_bucket, output_key)
    print(f"uploaded s3://{output_bucket}/{output_key}")


if __name__ == "__main__":
    # Anti-pattern: positional argv with no validation. A typo writes
    # production data to the wrong bucket and nobody notices until the
    # consumer service breaks.
    run(sys.argv[1], sys.argv[2], sys.argv[3])
