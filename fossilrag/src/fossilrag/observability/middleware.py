"""Request-correlation + security-header + per-request EMF metric middleware.

One ASGI middleware gives every request:
  * a correlation id (inbound ``X-Request-ID`` honoured, else generated) bound
    to a context var and echoed on the response,
  * baseline security response headers,
  * a structured access log line, and
  * an EMF latency + count metric dimensioned by endpoint (so all routes get
    CloudWatch metrics with zero per-handler code).

Liveness/root paths are excluded from metrics so the compose healthcheck poll
doesn't flood the metric stream.
"""

from __future__ import annotations

import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from fossilrag.logging import get_logger
from fossilrag.observability.metrics import emit_metric

log = get_logger("http")

REQUEST_ID_HEADER = "X-Request-ID"
_request_id: ContextVar[str] = ContextVar("request_id", default="-")

# Conservative, broadly-compatible headers for a JSON API. The meaningful
# Content-Security-Policy lives on nginx (which serves the HTML/SPA); these
# harden the API responses themselves.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}

_NO_METRIC_PATHS = {"/", "/healthz"}


def current_request_id() -> str:
    """The current request's correlation id (``-`` outside a request)."""
    return _request_id.get()


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # noqa: ANN001
        rid = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = _request_id.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            _request_id.reset(token)

        latency_ms = (time.perf_counter() - start) * 1000
        response.headers[REQUEST_ID_HEADER] = rid
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)

        path = request.url.path
        log.info(
            "event=http_request method=%s path=%s status=%d request_id=%s latency_ms=%.2f",
            request.method,
            path,
            response.status_code,
            rid,
            latency_ms,
        )
        if path not in _NO_METRIC_PATHS:
            emit_metric(
                "RequestLatencyMs",
                round(latency_ms, 2),
                unit="Milliseconds",
                dimensions={"Service": "fossilrag", "Endpoint": path},
                request_id=rid,
                status=response.status_code,
                method=request.method,
            )
        return response
