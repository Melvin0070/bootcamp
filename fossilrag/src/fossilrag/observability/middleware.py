"""Request-correlation + security-header + per-request EMF metric middleware.

One ASGI middleware gives every request:
  * a correlation id (inbound ``X-Request-ID`` honoured, else generated) bound
    to a context var, echoed on the response, and stamped on the access log line,
  * baseline security response headers,
  * a structured access log line, and
  * an EMF latency + count metric dimensioned by the matched **route template**
    (so all routes get CloudWatch metrics with zero per-handler code).

The metric dimension is the route *template*, never the raw path, so arbitrary
404 URLs (scanners, typos) collapse to one ``<unmatched>`` bucket instead of
minting an unbounded number of custom metrics. Liveness/root paths are excluded
from metrics so the compose healthcheck poll doesn't flood the metric stream.
The error path is recorded too, so a 500 still produces an access log + metric.
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
_UNMATCHED = "<unmatched>"


def current_request_id() -> str:
    """The current request's correlation id (``-`` outside a request)."""
    return _request_id.get()


def _route_template(request: Request) -> str:
    """The matched route's path template, or a sentinel for unmatched (404) paths.

    Keying metrics on the template (e.g. ``/excavate``) rather than the raw URL
    keeps custom-metric cardinality bounded — junk/scanner paths all map to one
    ``<unmatched>`` series.
    """
    route = request.scope.get("route")
    return getattr(route, "path", None) or _UNMATCHED


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # noqa: ANN001
        rid = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = _request_id.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Record the failure too (the case observability matters most), then
            # re-raise so Starlette's error handler produces the 500.
            self._record(request, rid, 500, (time.perf_counter() - start) * 1000)
            raise
        finally:
            _request_id.reset(token)

        latency_ms = (time.perf_counter() - start) * 1000
        response.headers[REQUEST_ID_HEADER] = rid
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        self._record(request, rid, response.status_code, latency_ms)
        return response

    @staticmethod
    def _record(request: Request, rid: str, status: int, latency_ms: float) -> None:
        # Logs keep the real path (no cardinality limit); the metric uses the
        # bounded route template.
        log.info(
            "event=http_request method=%s path=%s status=%d request_id=%s latency_ms=%.2f",
            request.method,
            request.url.path,
            status,
            rid,
            latency_ms,
        )
        endpoint = _route_template(request)
        if endpoint not in _NO_METRIC_PATHS:
            emit_metric(
                "RequestLatencyMs",
                round(latency_ms, 2),
                unit="Milliseconds",
                dimensions={"Service": "fossilrag", "Endpoint": endpoint},
                request_id=rid,
                status=status,
                method=request.method,
            )
