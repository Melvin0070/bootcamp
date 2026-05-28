"""AWS Lambda handler: S3 ObjectCreated → extract → silver.

Wired to the raw bucket's ``s3:ObjectCreated:*`` notification (Terraform in
PR11). For each event record it reads the raw object, extracts text +
provenance, and writes the silver-layer JSON. Extraction is content-addressed,
so redelivered events are idempotent.

Per-record failures are collected and, if any occurred, re-raised at the end so
the platform retries the invocation and (PR10) routes exhausted retries to the
DLQ — rather than silently dropping a document.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from fossilrag.config import get_settings
from fossilrag.ingest.extract import extract_document
from fossilrag.ingest.storage import make_s3_client, read_object, write_silver
from fossilrag.logging import configure_logging, get_logger

log = get_logger("ingest.handler")


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
        s3_info = record.get("s3", {})
        src_bucket = s3_info.get("bucket", {}).get("name")
        # S3 event keys are URL-encoded (spaces → '+', etc.).
        raw_key = s3_info.get("object", {}).get("key", "")
        key = urllib.parse.unquote_plus(raw_key)
        if not src_bucket or not key:
            log.warning("event=skip_malformed_record record=%r", record)
            continue
        try:
            data, content_type = read_object(src_bucket, key, client=s3)
            doc = extract_document(
                filename=key.rsplit("/", 1)[-1],
                data=data,
                content_type=content_type,
                source_uri=f"s3://{src_bucket}/{key}",
            )
            uri = write_silver(doc, silver_bucket, client=s3, prefix=settings.silver_prefix)
            processed.append({"key": key, "doc_id": doc.doc_id, "silver_uri": uri})
        except Exception as exc:  # noqa: BLE001 — record + continue, re-raise below
            log.exception("event=ingest_record_failed bucket=%s key=%s", src_bucket, key)
            failures.append({"key": key, "error": f"{type(exc).__name__}: {exc}"})

    log.info("event=ingest_batch_done processed=%d failed=%d", len(processed), len(failures))
    if failures:
        # Surface failure so the platform retries / DLQs (PR10), instead of a
        # silent partial success.
        raise RuntimeError(f"{len(failures)} record(s) failed: {failures}")
    return {"processed": processed, "failed": failures}
