"""AWS Lambda handler: S3 ObjectCreated → extract → silver.

A *direct* S3-ObjectCreated entrypoint. The deployed topology (Terraform, PR11)
routes the raw bucket through SQS to the resilient :mod:`fossilrag.worker.sqs`
worker instead — so this handler is currently **unwired**, kept as the
synchronous alternative (wire it by pointing the notification at this Lambda
rather than the queue). The per-object work is factored into
:func:`ingest_s3_object` so the SQS worker reuses exactly the same extraction
path, and both share the S3-event parser in :mod:`fossilrag.worker.events`.

Per-record *extraction* failures are collected and, if any occurred, re-raised
at the end so the platform retries the invocation and routes exhausted retries
to the DLQ. Structurally malformed event records (no bucket/key — not a
document) are logged and skipped, since retrying them can never succeed.
"""

from __future__ import annotations

from typing import Any

from fossilrag.config import get_settings
from fossilrag.events import pair_from_s3_record
from fossilrag.ingest.extract import extract_document
from fossilrag.ingest.storage import make_s3_client, read_object, write_silver
from fossilrag.logging import configure_logging, get_logger

log = get_logger("ingest.handler")


def ingest_s3_object(
    s3: Any, src_bucket: str, key: str, *, silver_bucket: str, prefix: str = "silver"
) -> dict[str, str]:
    """Read one raw S3 object, extract it, write the silver record. Returns refs.

    Uses the FULL key (not just the basename) as the doc_id filename input, so
    same-basename objects under different prefixes stay distinct fossils.
    """
    data, content_type = read_object(src_bucket, key, client=s3)
    doc = extract_document(
        filename=key,
        data=data,
        content_type=content_type,
        source_uri=f"s3://{src_bucket}/{key}",
    )
    uri = write_silver(doc, silver_bucket, client=s3, prefix=prefix)
    return {"key": key, "doc_id": doc.doc_id, "silver_uri": uri}


def _records(event: dict[str, Any]) -> list[dict[str, Any]]:
    return event.get("Records", []) if isinstance(event, dict) else []


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Process S3 ObjectCreated records into the silver layer."""
    settings = get_settings()
    configure_logging(settings.log_level)
    s3 = make_s3_client(settings)

    silver_bucket = settings.silver_bucket
    if not silver_bucket:
        raise RuntimeError("FOSSILRAG_SILVER_BUCKET is not set")

    processed: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []

    for record in _records(event):
        pair = pair_from_s3_record(record)
        if pair is None:
            log.warning("event=skip_malformed_record record=%r", record)
            continue
        src_bucket, key = pair
        try:
            processed.append(
                ingest_s3_object(
                    s3, src_bucket, key, silver_bucket=silver_bucket, prefix=settings.silver_prefix
                )
            )
        except Exception as exc:  # noqa: BLE001 — record + continue, re-raise below
            log.exception("event=ingest_record_failed bucket=%s key=%s", src_bucket, key)
            failures.append({"key": key, "error": f"{type(exc).__name__}: {exc}"})

    log.info("event=ingest_batch_done processed=%d failed=%d", len(processed), len(failures))
    if failures:
        # Surface failure so the platform retries / DLQs, instead of a silent
        # partial success.
        raise RuntimeError(f"{len(failures)} record(s) failed: {failures}")
    return {"processed": processed, "failed": failures}
