"""SQS-driven ingestion worker with partial-batch-failure + idempotency.

Decouples ingestion from S3 events for burst absorption: the raw bucket's
``s3:ObjectCreated:*`` notification fans into the queue (Terraform, PR11) and
this worker drains it. Message bodies are parsed by
:mod:`fossilrag.events`, which accepts both the S3-event-notification
shape S3 actually sends *and* a direct ``{"bucket","key"}`` producer task (and
skips S3's one-off ``s3:TestEvent``). It returns ``batchItemFailures`` so SQS
retries ONLY the failed messages (and DLQs them after maxReceiveCount) rather
than re-running the whole batch. The idempotency ledger skips already-processed
objects, so redeliveries are no-ops.
"""

from __future__ import annotations

import functools
from typing import Any

from fossilrag.config import get_settings
from fossilrag.events import parse_task_bodies
from fossilrag.idempotency import make_idempotency_store
from fossilrag.ingest.handler import ingest_s3_object
from fossilrag.ingest.storage import make_s3_client
from fossilrag.logging import configure_logging, get_logger
from fossilrag.worker.retry import retry_with_backoff

log = get_logger("worker.sqs")


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
            tasks = parse_task_bodies(record.get("body", ""))
        except Exception:  # noqa: BLE001 — unparseable body → DLQ, don't crash the batch
            log.exception("event=sqs_parse_failed message_id=%s", message_id)
            failures.append({"itemIdentifier": message_id})
            continue
        if not tasks:
            skipped += 1  # s3:TestEvent / no objects → nothing to do
            continue

        # A message may carry several S3 records; if ANY object fails, the whole
        # message is retried. Idempotency makes the already-done objects no-ops
        # on that retry, so partial progress within a message is never lost.
        message_failed = False
        for bucket, key in tasks:
            ikey = f"sqs:{bucket}:{key}"
            try:
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
            except Exception:  # noqa: BLE001 — failed object → message retried/DLQ'd by SQS
                log.exception("event=sqs_object_failed message_id=%s key=%s", message_id, key)
                message_failed = True
        if message_failed:
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
