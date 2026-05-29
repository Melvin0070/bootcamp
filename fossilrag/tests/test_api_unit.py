"""Unit tests for API wiring that don't need a database.

Importing ``fossilrag.api.app`` does NOT import asyncpg — the pgvector backend
is imported lazily inside ``make_vector_store`` — so these run anywhere and
cover the dependency-provider branches the e2e test can't easily reach.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from fossilrag.api.app import (
    IngestRequest,
    get_embedder,
    get_llm,
    get_prompt_cache,
    get_store,
    settings_dep,
)


def _request(**state) -> SimpleNamespace:
    """Minimal duck-typed stand-in for starlette.Request (we only touch app.state)."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state)))


def test_get_store_503_when_absent():
    with pytest.raises(HTTPException) as ei:
        get_store(_request(store=None))
    assert ei.value.status_code == 503


def test_get_embedder_503_when_absent():
    with pytest.raises(HTTPException) as ei:
        get_embedder(_request(embedder=None))
    assert ei.value.status_code == 503


def test_get_llm_503_when_absent():
    with pytest.raises(HTTPException) as ei:
        get_llm(_request(llm=None))
    assert ei.value.status_code == 503


def test_get_prompt_cache_503_when_absent():
    with pytest.raises(HTTPException) as ei:
        get_prompt_cache(_request(prompt_cache=None))
    assert ei.value.status_code == 503


def test_get_store_and_embedder_return_state():
    store, embedder, settings = object(), object(), object()
    req = _request(store=store, embedder=embedder, settings=settings)
    assert get_store(req) is store
    assert get_embedder(req) is embedder
    assert settings_dep(req) is settings


def test_ingest_request_rejects_blank_filename():
    with pytest.raises(ValidationError):
        IngestRequest(filename="", text="hello")


def test_ingest_request_defaults():
    req = IngestRequest(filename="a.txt", text="hello")
    assert req.layer_version == 1
    assert req.content_type == "text/plain"


def test_asgi_lambda_handler_builds():
    pytest.importorskip("mangum")
    from fossilrag.api.asgi import handler  # Mangum(app) — the API Lambda entrypoint

    assert callable(handler)
