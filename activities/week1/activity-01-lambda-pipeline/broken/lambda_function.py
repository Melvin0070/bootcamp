"""
BROKEN: Serverless document ingestion Lambda.

Known issues (do not fix here — this is the "before" state):
  1. Timeout: processes one file fully per message — large files will hit the 3s limit.
  2. No error handling: one bad record crashes the entire batch; SQS retries ALL records.
  3. No partial batch response: successful records get reprocessed on every retry.
  4. Hardcoded bucket names: breaks in every environment other than dev.
  5. No logging: impossible to debug failures in CloudWatch.
  6. No idempotency: re-running re-processes every document.
  7. Blocking sleep simulates a slow external API call that causes timeouts.
"""

import json
import time
import re
import boto3

# Hardcoded — breaks in staging/prod
s3 = boto3.client('s3')
RAW_BUCKET = 'fossils-raw-bucket'
SILVER_BUCKET = 'fossils-silver-bucket'


def handler(event, context):
    for record in event['Records']:
        body = json.loads(record['body'])
        key = body['s3_key']

        # Downloads entire file synchronously — large files cause timeout
        obj = s3.get_object(Bucket=RAW_BUCKET, Key=key)
        raw_text = obj['Body'].read().decode('utf-8')

        # Simulates slow processing (e.g., calling an external API)
        # On a real 100KB document this would push past the 3s timeout
        cleaned = _clean_text(raw_text)

        output_key = f'silver/{key}'
        s3.put_object(
            Bucket=SILVER_BUCKET,
            Key=output_key,
            Body=cleaned.encode('utf-8'),
        )

    # Always returns 200 — even when records failed
    return {'statusCode': 200}


def _clean_text(text: str) -> str:
    # Artificially slow — simulates blocking I/O or a heavy regex pass
    time.sleep(2)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()
