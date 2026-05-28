"""S3 I/O for the ingestion pipeline: read raw objects, write the silver layer.

``boto3`` is optional (the ``aws`` extra) and imported lazily so importing the
package never requires it. Tests drive this with ``moto`` (in-process, $0, no
account/token); the compose demo points it at LocalStack via the standard
``AWS_ENDPOINT_URL`` (boto3 respects it automatically) or an explicit endpoint.

The silver key is content-addressed (``<prefix>/<doc_id>.json``), so
re-ingesting identical bytes overwrites the same object — idempotent by
construction, consistent with the rest of the pipeline.
"""

from __future__ import annotations

from typing import Any

from fossilrag.config import Settings, get_settings
from fossilrag.logging import get_logger
from fossilrag.models import RawDocument

log = get_logger("ingest.storage")


def make_s3_client(settings: Settings | None = None, *, client: Any = None) -> Any:
    """Build (or accept an injected) boto3 S3 client.

    Honours ``aws_endpoint_url`` (LocalStack) when set; otherwise boto3's
    default resolution applies (incl. the ``AWS_ENDPOINT_URL`` env var, which
    LocalStack sets inside its own Lambdas).
    """
    if client is not None:
        return client
    import boto3  # lazy: optional 'aws' dependency

    settings = settings or get_settings()
    kwargs: dict[str, Any] = {"region_name": settings.aws_region}
    if settings.aws_endpoint_url:
        kwargs["endpoint_url"] = settings.aws_endpoint_url
    return boto3.client("s3", **kwargs)


def read_object(bucket: str, key: str, *, client: Any) -> tuple[bytes, str]:
    """Fetch an object's bytes and its reported content type."""
    resp = client.get_object(Bucket=bucket, Key=key)
    body = resp["Body"].read()
    content_type = resp.get("ContentType", "application/octet-stream")
    log.info("event=s3_object_read bucket=%s key=%s bytes=%d", bucket, key, len(body))
    return body, content_type


def silver_key(doc_id: str, prefix: str = "silver") -> str:
    return f"{prefix.rstrip('/')}/{doc_id}.json"


def write_silver(
    doc: RawDocument,
    bucket: str,
    *,
    client: Any,
    prefix: str = "silver",
) -> str:
    """Persist a :class:`RawDocument` as silver-layer JSON. Returns the s3 URI."""
    key = silver_key(doc.doc_id, prefix)
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=doc.model_dump_json(indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    uri = f"s3://{bucket}/{key}"
    log.info("event=silver_written doc_id=%s uri=%s chars=%d", doc.doc_id[:12], uri, doc.char_count)
    return uri
