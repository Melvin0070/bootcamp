"""Endpoint-aware boto3 client factory (LocalStack-friendly).

Mirrors :func:`fossilrag.ingest.storage.make_s3_client` for the other services
the bootstrap/poller need (sqs, dynamodb): honour ``aws_endpoint_url`` when set
(LocalStack), otherwise fall back to boto3's default resolution — which already
respects the ``AWS_ENDPOINT_URL`` env var globally, so inside the compose
``aws`` profile every client points at LocalStack with no per-call config.

Tests inject their own (moto) clients via ``client=``; the real-boto3 path is
exercised only against LocalStack/AWS and is excluded from coverage.
"""

from __future__ import annotations

from typing import Any

from fossilrag.config import Settings, get_settings


def _boto3_client(service: str, region: str, endpoint_url: str | None) -> Any:  # pragma: no cover
    import boto3

    kwargs: dict[str, Any] = {"region_name": region}
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return boto3.client(service, **kwargs)


def make_client(service: str, settings: Settings | None = None, *, client: Any = None) -> Any:
    """Build (or accept an injected) boto3 client for ``service``.

    Pass ``client=`` to inject a fake/moto client (the unit-test path); omit it
    to construct a real client honouring ``aws_region`` + ``aws_endpoint_url``.
    """
    if client is not None:
        return client
    settings = settings or get_settings()  # pragma: no cover - real AWS/LocalStack only
    return _boto3_client(
        service, settings.aws_region, settings.aws_endpoint_url
    )  # pragma: no cover
