"""
Secrets resolution with a layered lookup strategy.

`get_secret(name)` walks a deterministic chain:

    1. Process env var with the exact name (e.g. `OPENAI_API_KEY`).
    2. AWS Secrets Manager `GetSecretValue` for `secret_id` (defaults to
       `{SECRETS_PREFIX}/{name.lower()}`, e.g. `fossilrag/prod/openai_api_key`).
    3. Raise `SecretNotFoundError` — fail loudly, never default to "".

The result is cached in-memory for `cache_ttl_sec` (default 300 s) so a
hot path doesn't slam Secrets Manager. The cache key is the secret name,
so rotating a secret in AWS is picked up after at most one TTL cycle
without a redeploy.

Why this layering:

  * **Local dev** sets env vars from a (gitignored) `.env`. Zero AWS calls.
  * **Production** uses an IAM role with `secretsmanager:GetSecretValue`
    on a single ARN — no static AWS keys anywhere in the system.
  * **CI** can override any secret with an env var without touching AWS.

What this module deliberately does NOT do:

  * Log secret values. Even at DEBUG level, only the name + first 4 chars
    of the resolved value are ever logged (and only when explicitly
    requested via `LOG_SECRET_PROBE=1` for debugging).
  * Persist secrets to disk. The cache is process-local; a SIGTERM clears it.
  * Fall back to a default value. Defaults silently mask configuration
    errors and let production limp along on stale or wrong credentials.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

log = logging.getLogger("secrets")

DEFAULT_PREFIX = os.environ.get("SECRETS_PREFIX", "fossilrag/prod")
DEFAULT_CACHE_TTL_SEC = 300


class SecretNotFoundError(LookupError):
    """Raised when a secret can't be resolved from any layer."""


class _Cache:
    """Tiny TTL cache — name → (value, expires_at)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[str, float]] = {}

    def get(self, name: str) -> str | None:
        now = time.time()
        with self._lock:
            entry = self._entries.get(name)
            if entry is None:
                return None
            value, expires_at = entry
            if expires_at < now:
                del self._entries[name]
                return None
            return value

    def set(self, name: str, value: str, ttl_sec: float) -> None:
        with self._lock:
            self._entries[name] = (value, time.time() + ttl_sec)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_CACHE = _Cache()


def _redact(value: str) -> str:
    """Show only the first 4 chars of a secret for log probes."""
    if not value:
        return "<empty>"
    return value[:4] + "…" + f"({len(value)} chars)"


def _from_env(name: str) -> str | None:
    """Layer 1 — direct env var lookup."""
    return os.environ.get(name)


def _from_secrets_manager(secret_id: str, *, client: Any | None = None) -> str | None:
    """Layer 2 — AWS Secrets Manager.

    The boto3 client is constructed with NO explicit credentials so it picks
    up the default provider chain (IAM role on EC2/ECS/EKS/Lambda, or
    `~/.aws/credentials` for local dev). If you find yourself passing
    aws_access_key_id here, you have re-introduced the bug this module
    was written to fix.
    """
    if client is None:
        try:
            import boto3
        except ImportError:
            log.warning("event=secrets_manager_unavailable reason=boto3_not_installed")
            return None
        client = boto3.client(
            "secretsmanager",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )

    try:
        resp = client.get_secret_value(SecretId=secret_id)
    except Exception as e:
        # Any SM failure: log the *type* of failure, never the secret id at
        # ERROR level (the id can leak via metric exemplars). At DEBUG it's
        # safe to include because operators are reading source-of-truth ids
        # at debug time.
        log.warning(
            "event=secrets_manager_failed error_type=%s",
            type(e).__name__,
        )
        log.debug("event=secrets_manager_failed secret_id=%s", secret_id)
        return None

    # Secrets Manager returns either SecretString or SecretBinary.
    if "SecretString" in resp:
        raw = resp["SecretString"]
    else:
        raw = resp["SecretBinary"].decode("utf-8")

    # Some shops store one secret per ARN; others store a JSON map. Support
    # both: if it's a JSON object with one key, return that value; else the
    # raw string.
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and len(parsed) == 1:
            return next(iter(parsed.values()))
    except (ValueError, TypeError):
        pass
    return raw


def get_secret(
    name: str,
    *,
    secret_id: str | None = None,
    cache_ttl_sec: float = DEFAULT_CACHE_TTL_SEC,
    sm_client: Any | None = None,
) -> str:
    """Resolve `name` via the env → Secrets Manager chain.

    `secret_id` defaults to `f"{SECRETS_PREFIX}/{name.lower()}"`. Pass
    explicitly if your secret naming convention differs (e.g. you keep a
    single JSON map under one ARN).
    """
    cached = _CACHE.get(name)
    if cached is not None:
        if os.environ.get("LOG_SECRET_PROBE") == "1":
            log.debug("event=secret_cache_hit name=%s value=%s", name, _redact(cached))
        return cached

    value = _from_env(name)
    if value is not None:
        log.info("event=secret_resolved name=%s source=env", name)
        _CACHE.set(name, value, cache_ttl_sec)
        return value

    sid = secret_id or f"{DEFAULT_PREFIX}/{name.lower()}"
    value = _from_secrets_manager(sid, client=sm_client)
    if value is not None:
        log.info("event=secret_resolved name=%s source=secrets_manager", name)
        _CACHE.set(name, value, cache_ttl_sec)
        return value

    log.error("event=secret_not_found name=%s tried=env,secrets_manager", name)
    raise SecretNotFoundError(
        f"secret {name!r} not found in env or AWS Secrets Manager "
        f"(tried secret_id={sid!r})"
    )


def clear_cache() -> None:
    """Drop every cached secret. Use in tests or after a manual rotation."""
    _CACHE.clear()
