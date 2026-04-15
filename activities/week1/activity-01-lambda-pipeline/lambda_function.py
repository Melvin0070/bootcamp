"""
FIXED: Serverless document ingestion Lambda.

What changed and why:
  1. Timeout raised to 60s in template — gives documents room to breathe.
  2. Partial batch response (ReportBatchItemFailures) — only failed records
     go back to the queue; successful ones are not reprocessed.
  3. All config via environment variables — no hardcoded bucket names.
  4. Structured JSON logging — every event carries correlation IDs that
     CloudWatch Logs Insights can query.
  5. Idempotency via DynamoDB — re-running skips already-processed documents.
  6. Error isolation — one bad record is caught and reported; others continue.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Logging — emit JSON so CloudWatch Logs Insights can filter by field
# ---------------------------------------------------------------------------
logger = logging.getLogger()
logger.setLevel(os.environ.get('LOG_LEVEL', 'INFO'))

# ---------------------------------------------------------------------------
# Clients — created once at module level, reused across warm invocations
# ---------------------------------------------------------------------------
s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

# ---------------------------------------------------------------------------
# Config — all from environment variables, never hardcoded
# ---------------------------------------------------------------------------
RAW_BUCKET = os.environ['RAW_BUCKET']
SILVER_BUCKET = os.environ['SILVER_BUCKET']
IDEMPOTENCY_TABLE = os.environ.get('IDEMPOTENCY_TABLE', '')


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handler(event: dict, context) -> dict:
    """
    Process a batch of SQS messages.

    Each message body must be JSON with an 's3_key' field pointing to a raw
    document in RAW_BUCKET. Cleaned text is written to SILVER_BUCKET under
    the 'silver/' prefix.

    Returns a partial batch response so SQS only retries failed messages.
    """
    total = len(event['Records'])
    batch_item_failures = []

    logger.info(json.dumps({
        'event': 'batch_start',
        'batch_size': total,
        'function': context.function_name,
        'remaining_ms': context.get_remaining_time_in_millis(),
    }))

    for record in event['Records']:
        message_id = record['messageId']
        try:
            body = json.loads(record['body'])
            s3_key = body['s3_key']
            _process_document(s3_key, message_id)
        except Exception as exc:
            # Log the failure but let other records continue
            logger.error(json.dumps({
                'event': 'record_failed',
                'message_id': message_id,
                'error': str(exc),
                'error_type': type(exc).__name__,
            }))
            # Report this message ID so SQS re-delivers only this record
            batch_item_failures.append({'itemIdentifier': message_id})

    failed = len(batch_item_failures)
    logger.info(json.dumps({
        'event': 'batch_complete',
        'total': total,
        'succeeded': total - failed,
        'failed': failed,
    }))

    # Partial batch response — empty list means all succeeded
    return {'batchItemFailures': batch_item_failures}


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

def _process_document(s3_key: str, message_id: str) -> None:
    """Download, clean, and store one document."""

    # Idempotency: skip documents we have already processed
    if IDEMPOTENCY_TABLE and _already_processed(s3_key):
        logger.info(json.dumps({
            'event': 'skip_duplicate',
            'message_id': message_id,
            's3_key': s3_key,
        }))
        return

    raw_text = _download(s3_key)
    cleaned_text = clean_text(raw_text)
    _upload(f'silver/{s3_key}', cleaned_text)

    if IDEMPOTENCY_TABLE:
        _mark_processed(s3_key)

    logger.info(json.dumps({
        'event': 'record_success',
        'message_id': message_id,
        's3_key': s3_key,
        'raw_bytes': len(raw_text),
        'clean_bytes': len(cleaned_text),
    }))


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------

def _download(s3_key: str) -> str:
    """Download a document from the raw S3 bucket."""
    try:
        response = s3.get_object(Bucket=RAW_BUCKET, Key=s3_key)
        return response['Body'].read().decode('utf-8')
    except ClientError as exc:
        logger.error(json.dumps({
            'event': 's3_download_error',
            's3_key': s3_key,
            'error': exc.response['Error']['Code'],
        }))
        raise


def _upload(s3_key: str, content: str) -> None:
    """Write cleaned text to the silver S3 bucket."""
    try:
        s3.put_object(
            Bucket=SILVER_BUCKET,
            Key=s3_key,
            Body=content.encode('utf-8'),
            ContentType='text/plain',
        )
    except ClientError as exc:
        logger.error(json.dumps({
            'event': 's3_upload_error',
            's3_key': s3_key,
            'error': exc.response['Error']['Code'],
        }))
        raise


# ---------------------------------------------------------------------------
# Text cleaning — pure function, easy to unit-test
# ---------------------------------------------------------------------------

def clean_text(text: str) -> str:
    """
    Remove control characters and normalise whitespace.

    Steps:
      1. Strip ASCII control chars (except tab, newline, carriage return).
      2. Normalise line endings to \\n.
      3. Collapse multiple spaces into one.
      4. Strip leading/trailing whitespace.
    """
    # Remove non-printable control characters (keep \\t \\n \\r)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    # Normalise Windows / old Mac line endings
    text = re.sub(r'\r\n|\r', '\n', text)
    # Collapse runs of spaces (but not newlines)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()


# ---------------------------------------------------------------------------
# DynamoDB idempotency helpers
# ---------------------------------------------------------------------------

def _already_processed(s3_key: str) -> bool:
    """Return True if this document is already in the idempotency table."""
    try:
        table = dynamodb.Table(IDEMPOTENCY_TABLE)
        response = table.get_item(Key={'s3_key': s3_key})
        return 'Item' in response
    except ClientError:
        # Fail open: if DynamoDB is unavailable, process the document anyway
        logger.warning(json.dumps({
            'event': 'idempotency_check_failed',
            's3_key': s3_key,
            'action': 'proceeding_with_processing',
        }))
        return False


def _mark_processed(s3_key: str) -> None:
    """Record a successfully processed document in the idempotency table."""
    try:
        now = datetime.now(timezone.utc)
        ttl = int(now.timestamp()) + (30 * 24 * 3600)  # expire after 30 days
        table = dynamodb.Table(IDEMPOTENCY_TABLE)
        table.put_item(Item={
            's3_key': s3_key,
            'processed_at': now.isoformat(),
            'ttl': ttl,
        })
    except ClientError as exc:
        # Non-fatal: log and continue — the document was processed successfully
        logger.warning(json.dumps({
            'event': 'idempotency_write_failed',
            's3_key': s3_key,
            'error': str(exc),
        }))
