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

import contextlib
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

    # Indexed mode: run the FULL pipeline per object (extract → chunk → embed →
    # upsert) so an S3 upload becomes searchable end-to-end, not just written to
    # silver. Needs DATABASE_URL + an embedder; gated so the default worker
    # stays lean + DB-free.
    if settings.worker_index:
        import asyncio

        return asyncio.run(_index_batch(event, settings, s3, idem, silver_bucket))

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


async def _index_object(
    s3: Any, store: Any, embedder: Any, bucket: str, key: str, *, silver_bucket: str, prefix: str
) -> int:
    """Read one raw object, write silver, and embed+upsert it into the vector
    store so it is immediately searchable. Returns the chunk count."""
    from fossilrag.chunking import chunk_document
    from fossilrag.ingest.extract import extract_document
    from fossilrag.ingest.storage import read_object, write_silver

    data, content_type = read_object(bucket, key, client=s3)
    doc = extract_document(
        filename=key, data=data, content_type=content_type, source_uri=f"s3://{bucket}/{key}"
    )
    write_silver(doc, silver_bucket, client=s3, prefix=prefix)  # keep the medallion silver layer
    chunks = chunk_document(doc, layer_version=1)
    if chunks:
        vectors = embedder.encode([c.content for c in chunks])
        await store.upsert_chunks(chunks, vectors)
    return len(chunks)


async def _index_batch(
    event: dict[str, Any], settings: Any, s3: Any, idem: Any, silver_bucket: str
) -> dict[str, Any]:
    """Indexed-mode batch handler: same partial-batch + idempotency contract as
    the sync path, but each object runs the full extract→…→upsert pipeline."""
    from fossilrag.embedding import make_embedder
    from fossilrag.vectorstore import make_vector_store

    embedder = make_embedder(settings)
    store = await make_vector_store(settings)
    failures: list[dict[str, str]] = []
    processed = skipped = 0
    try:
        for record in event.get("Records", []):
            message_id = record.get("messageId", "")
            try:
                tasks = parse_task_bodies(record.get("body", ""))
            except Exception:  # noqa: BLE001 — unparseable → DLQ, don't crash the batch
                log.exception("event=sqs_parse_failed message_id=%s", message_id)
                failures.append({"itemIdentifier": message_id})
                continue
            if not tasks:
                skipped += 1
                continue
            message_failed = False
            for bucket, key in tasks:
                ikey = f"sqs:{bucket}:{key}"
                try:
                    if idem.is_processed(ikey):
                        skipped += 1
                        continue
                    await _index_object(
                        s3,
                        store,
                        embedder,
                        bucket,
                        key,
                        silver_bucket=silver_bucket,
                        prefix=settings.silver_prefix,
                    )
                    idem.mark_processed(ikey, f"indexed:{key}")
                    processed += 1
                except Exception:  # noqa: BLE001 — failed object → message retried/DLQ'd by SQS
                    log.exception("event=sqs_index_failed message_id=%s key=%s", message_id, key)
                    message_failed = True
            if message_failed:
                failures.append({"itemIdentifier": message_id})
    finally:
        with contextlib.suppress(Exception):
            await store.close()
    log.info(
        "event=sqs_batch_done mode=index processed=%d skipped=%d failed=%d",
        processed,
        skipped,
        len(failures),
    )
    return {"batchItemFailures": failures}
