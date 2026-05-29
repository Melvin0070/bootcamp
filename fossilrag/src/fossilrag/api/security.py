"""Optional API-key authentication.

Off by default (``FOSSILRAG_API_KEY`` unset) so local/demo runs need no
credential. When the key IS set, every endpoint except the liveness/root paths
requires a matching ``X-API-Key`` header. Wired as a global app dependency, so
new endpoints are protected automatically.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

# Open paths so the compose healthcheck + service discovery work unauthenticated.
_OPEN_PATHS = {"/", "/healthz"}


async def require_api_key(request: Request) -> None:
    """Enforce ``X-API-Key`` iff an API key is configured. No-op otherwise."""
    if request.method == "OPTIONS" or request.url.path in _OPEN_PATHS:
        return
    settings = getattr(request.app.state, "settings", None)
    expected = getattr(settings, "api_key", None)
    if not expected:
        return  # auth disabled — the demo/default posture
    provided = request.headers.get("X-API-Key", "")
    # Constant-time compare so a wrong key can't be timed out byte-by-byte.
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid or missing API key")
