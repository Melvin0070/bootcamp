"""
FossilRAG ingestion pipeline — BROKEN baseline (zero observability).

This is the "before" state for Activity 11. The pipeline runs daily as
a Lambda triggered by an S3 ObjectCreated event, normalises a CSV of
fossil records, and writes a Parquet to a downstream bucket. Same
business logic as Activity 10's pipeline, but the operational layer
is entirely missing.

Anti-patterns deliberately preserved
------------------------------------
1. ZERO metrics. Lambda's built-in `Invocations` / `Errors` /
   `Duration` are the only signal. There is no failure-rate metric
   broken down by stage (read / normalise / write / upload), no
   per-stage latency, no row-count gauge — so when the consumer
   reports "the output looks short," there is no way to tell which
   stage truncated it.
2. NO alarms. The downstream BI team discovers failures by noticing
   their dashboard is stale on Monday morning. MTTD is measured in
   business days.
3. NO dashboard. To answer "is the pipeline healthy right now?" the
   on-call SRE has to open the Lambda console, scroll the metrics
   tab, then cross-reference CloudWatch Logs by hand.
4. `print()` everywhere. CloudWatch Logs Insights cannot parse the
   plain text, so queries like "show me the last 10 failures with
   row counts" are impossible without a regex grok pattern that
   nobody has written.
5. Exceptions are SWALLOWED with a bare `except: pass`. The Lambda
   returns 200 OK even when normalise() throws, so even the built-in
   `Errors` metric stays at zero. The failure surfaces three steps
   downstream as "empty parquet."
6. Latency is measured with `print(time.time() - t0)`. The number
   exists in the logs but not as a metric, so you cannot alarm on
   p99 > 30 s and you cannot graph the trend.
7. The Lambda handler has no `request_id` / `correlation_id` in its
   log lines. Tracing one bad fossil through ingest → enrich → embed
   means manually joining three log groups by timestamp.

The fix in `pipeline.py` (one level up) keeps the same business logic
but emits CloudWatch Embedded Metric Format (EMF) records, ships a
CloudFormation template that defines alarms and a dashboard, and uses
structured key=value logging so Logs Insights queries work without a
grok pattern.
"""

import json
import sys
import time

import boto3
import pandas as pd


def handler(event, context):
    """S3-triggered Lambda. Returns 200 OK no matter what — that is the bug."""
    t0 = time.time()
    print(f"start event={json.dumps(event)}")

    try:
        # Anti-pattern: tightly coupled — read, transform, and write
        # all share one try/except. There is no way to attribute a
        # failure to a stage.
        bucket = event["Records"][0]["s3"]["bucket"]["name"]
        key = event["Records"][0]["s3"]["object"]["key"]

        s3 = boto3.client("s3")
        # Anti-pattern: new S3 client per invocation (cold-start cost
        # is invisible because we never measured it).
        obj = s3.get_object(Bucket=bucket, Key=key)
        df = pd.read_csv(obj["Body"])
        print(f"read rows={len(df)}")

        # Business logic.
        df = df.rename(columns={"Species": "species", "Age_MYA": "age_mya"})
        df["species"] = df["species"].str.strip().str.lower()
        df = df.dropna(subset=["species", "age_mya"])
        print(f"normalised rows={len(df)}")

        out = "/tmp/out.parquet"
        df.to_parquet(out, index=False)
        s3.upload_file(out, bucket, key.replace(".csv", ".parquet"))

        # Anti-pattern: latency is printed, not metricised. You can
        # eyeball it in the logs; you cannot alarm on it.
        print(f"done latency_s={time.time() - t0:.2f}")
        return {"statusCode": 200}

    except Exception:
        # Anti-pattern: bare except + pass + 200 OK. The Lambda
        # invocation counter stays clean; nothing alarms; the
        # downstream parquet is missing or stale; nobody notices
        # until Monday.
        pass

    return {"statusCode": 200}


# Local smoke harness — `python pipeline.py path/to/test_event.json`.
if __name__ == "__main__":
    event = json.load(open(sys.argv[1]))
    print(handler(event, None))
