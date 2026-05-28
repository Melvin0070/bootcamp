"""SQS-driven ingestion worker with partial-batch-failure + idempotency.

Decouples ingestion from S3 events for burst absorption: an S3 notification (or
a producer) enqueues ``{"bucket","key"}`` tasks; this worker drains them. It
returns ``batchItemFailures`` so SQS retries ONLY the failed messages (and DLQs
them after maxReceiveCount) rather than re-running the whole batch. The
idempotency ledger skips already-processed objects, so redeliveries are no-ops.
"""

from __future__ import annotations

import functools
import json
from typing import Any

from fossilrag.config import get_settings
from fossilrag.idempotency import make_idempotency_store
from fossilrag.ingest.handler import ingest_s3_object
from fossilrag.ingest.storage import make_s3_client
from fossilrag.logging import configure_logging, get_logger
from fossilrag.worker.retry import retry_with_backoff

log = get_logger("worker.sqs")


def _task(body: str) -> tuple[str, str]:
    """Parse an SQS task body ``{"bucket": ..., "key": ...}``."""
    data = json.loads(body)
    return data["bucket"], data["key"]


def sqs_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Process SQS ingestion tasks; return partial-batch-failure identifiers."""
    settings = get_settings()
    configure_logging(settings.log_level)
    s3 = make_s3_client(settings)
    idem = make_idempotency_store(settings)
    silver_bucket = settings.silver_bucket
    if not silver_bucket:
        raise RuntimeError("FOSSILRAG_SILVER_BUCKET is not set")

    failures: list[dict[str, str]] = []
    processed = skipped = 0

    for record in event.get("Records", []):
        message_id = record.get("messageId", "")
        try:
            bucket, key = _task(record.get("body", ""))
            ikey = f"sqs:{bucket}:{key}"
            if idem.is_processed(ikey):
                skipped += 1  # redelivered / already done → idempotent no-op
                continue
            retry_with_backoff(
                functools.partial(
                    ingest_s3_object,
                    s3,
                    bucket,
                    key,
                    silver_bucket=silver_bucket,
                    prefix=settings.silver_prefix,
                ),
                max_attempts=settings.sqs_max_attempts,
                base_delay=settings.sqs_base_delay,
            )
            idem.mark_processed(ikey, f"silver:{key}")
            processed += 1
        except Exception:  # noqa: BLE001 — failed message → retried/DLQ'd by SQS
            log.exception("event=sqs_record_failed message_id=%s", message_id)
            failures.append({"itemIdentifier": message_id})

    log.info(
        "event=sqs_batch_done processed=%d skipped=%d failed=%d",
        processed,
        skipped,
        len(failures),
    )
    # AWS Lambda SQS partial-batch-response shape; with ReportBatchItemFailures
    # configured, only these messages are returned to the queue for retry.
    return {"batchItemFailures": failures}
