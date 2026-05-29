"""EMF metrics + request-context/security-header middleware."""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fossilrag.observability.metrics import build_emf, emit_metric
from fossilrag.observability.middleware import (
    REQUEST_ID_HEADER,
    SECURITY_HEADERS,
    RequestContextMiddleware,
)


def test_build_emf_shape():
    doc = build_emf(
        {"LatencyMs": 12.5},
        units={"LatencyMs": "Milliseconds"},
        dimensions={"Service": "fossilrag", "Endpoint": "/excavate"},
        properties={"status": 200},
        timestamp_ms=1000,
    )
    aws = doc["_aws"]
    assert aws["Timestamp"] == 1000
    cw = aws["CloudWatchMetrics"][0]
    assert cw["Namespace"] == "FossilRAG"
    assert cw["Dimensions"] == [["Service", "Endpoint"]]
    assert {"Name": "LatencyMs", "Unit": "Milliseconds"} in cw["Metrics"]
    # Dimension + metric + property values are sibling top-level keys.
    assert doc["LatencyMs"] == 12.5
    assert doc["Service"] == "fossilrag"
    assert doc["Endpoint"] == "/excavate"
    assert doc["status"] == 200


def test_build_emf_no_dimensions_is_valid():
    doc = build_emf({"Count": 1}, timestamp_ms=1)
    assert doc["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [[]]


def test_emit_metric_returns_serialisable_doc():
    doc = emit_metric("Hits", 3, dimensions={"Service": "fossilrag"}, request_id="r1")
    assert doc["Hits"] == 3
    assert doc["request_id"] == "r1"
    json.dumps(doc)  # must be JSON-serialisable (it's logged as one line)


def _mini_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/ping")
    def ping() -> dict:
        return {"ok": True}

    return app


def test_request_id_generated_and_echoed():
    client = TestClient(_mini_app())
    r = client.get("/ping")
    assert r.status_code == 200
    assert r.headers.get(REQUEST_ID_HEADER)


def test_inbound_request_id_is_honoured():
    client = TestClient(_mini_app())
    r = client.get("/ping", headers={REQUEST_ID_HEADER: "trace-abc"})
    assert r.headers[REQUEST_ID_HEADER] == "trace-abc"


def test_security_headers_present():
    client = TestClient(_mini_app())
    r = client.get("/ping")
    for header in SECURITY_HEADERS:
        assert header in r.headers
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_unmatched_path_metric_collapses_to_sentinel(caplog):
    # A 404 on an arbitrary path must NOT mint a per-URL custom metric; it maps
    # to the bounded "<unmatched>" bucket (route template, not the raw path).
    client = TestClient(_mini_app())
    with caplog.at_level(logging.INFO):
        r = client.get("/no/such/route")
    assert r.status_code == 404
    # The EMF metric's Endpoint dimension is the sentinel, not the raw URL.
    assert '"Endpoint":"<unmatched>"' in caplog.text


def test_error_path_is_recorded_then_reraised(caplog):
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/boom")
    def boom() -> dict:
        raise ValueError("kaboom")

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.INFO):
        r = client.get("/boom")
    assert r.status_code == 500
    # The failure still produced an access log line (status=500), not silence.
    assert "event=http_request" in caplog.text
    assert "status=500" in caplog.text
