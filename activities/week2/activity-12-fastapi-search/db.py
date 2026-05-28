"""
Connection-pool lifecycle for the FastAPI /search service.

Why asyncpg (vs SQLAlchemy async vs aiopg)
------------------------------------------
- Fastest of the three by a wide margin — asyncpg implements the
  Postgres binary protocol directly, no psycopg layer.
- Native pool with bounded `min_size` / `max_size` and proper
  health-checks (`server_settings`, `max_inactive_connection_lifetime`).
- The async API is the only API — no risk of someone calling a sync
  method by accident from an async handler.

The pool is created once at FastAPI lifespan startup and shared
across all request handlers. Connections are checked out per-request
with `async with pool.acquire()` and returned on context exit; the
pool handles broken-connection detection and replacement.

Defensive defaults
------------------
- `command_timeout` caps any single query at 5 s — bounds the
  blast radius of a slow query so one bad request can't hold a
  connection forever.
- `min_size=5` keeps a warm baseline of connections so the first
  burst of requests doesn't pay the full handshake cost.
- `max_size=20` leaves headroom under Postgres's default
  `max_connections=100` even with 4 Uvicorn workers (4 × 20 = 80).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import asyncpg

log = logging.getLogger("fossilrag.search")

# Pool sizing — read once at startup. Tuned for a single Postgres
# with `max_connections=100` and up to 4 Uvicorn workers.
POOL_MIN_SIZE = int(os.environ.get("POOL_MIN_SIZE", "5"))
POOL_MAX_SIZE = int(os.environ.get("POOL_MAX_SIZE", "20"))
COMMAND_TIMEOUT_SEC = float(os.environ.get("COMMAND_TIMEOUT_SEC", "5.0"))


async def create_pool(dsn: str | None = None) -> asyncpg.Pool:
    """Create the connection pool.

    Pulled into its own function (rather than inlined into lifespan)
    so the test suite can build the pool against a testcontainers
    Postgres without spinning up the full FastAPI app.
    """
    dsn = dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        # Fail loud at startup; do not let a misconfigured DSN leak
        # into every request handler the way the broken baseline did.
        raise RuntimeError("DATABASE_URL is not set")

    # Validate the env-derived sizes up front with a clear message,
    # rather than letting asyncpg raise a less obvious error deeper in.
    if POOL_MIN_SIZE < 0 or POOL_MAX_SIZE <= 0:
        raise ValueError(f"pool sizes must be positive (min={POOL_MIN_SIZE}, max={POOL_MAX_SIZE})")
    if POOL_MIN_SIZE > POOL_MAX_SIZE:
        raise ValueError(
            f"POOL_MIN_SIZE ({POOL_MIN_SIZE}) cannot exceed POOL_MAX_SIZE ({POOL_MAX_SIZE})"
        )
    if COMMAND_TIMEOUT_SEC <= 0:
        raise ValueError(f"COMMAND_TIMEOUT_SEC must be positive (got {COMMAND_TIMEOUT_SEC})")

    pool = await asyncpg.create_pool(
        dsn,
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
        command_timeout=COMMAND_TIMEOUT_SEC,
        # asyncpg will recycle a connection that's been idle this long
        # — protects against TCP keepalive expiry on long-lived NATs.
        max_inactive_connection_lifetime=300,
    )
    log.info(
        "event=pool_created min_size=%d max_size=%d command_timeout=%.1fs",
        POOL_MIN_SIZE,
        POOL_MAX_SIZE,
        COMMAND_TIMEOUT_SEC,
    )
    return pool


async def search_specimens(
    pool: asyncpg.Pool,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Search specimens by species name (case-insensitive substring).

    Same user-facing semantics as the broken baseline (substring
    match), but the pg_trgm GIN index on ``species`` (migrations/0001)
    turns this ILIKE into a Bitmap Index Scan instead of the
    sequential scan the broken baseline forced. A leading-wildcard
    substring ILIKE can't use a plain btree index — pg_trgm is the
    extension that makes substring search indexable.

    Injection-safe by construction: the ``%...%`` wildcards live in the
    BOUND VALUE (``$1``), not the SQL text. asyncpg binds ``$1 / $2``
    natively, so a payload like ``'; DROP TABLE`` is matched as a
    literal string, never executed.
    """
    sql = """
        SELECT id, species, age_mya, notes
        FROM specimens
        WHERE species ILIKE $1
        ORDER BY species
        LIMIT $2
    """
    # Wildcards are part of the bound parameter value, not the SQL — so
    # string concatenation here is safe (no f-string into the query).
    pattern = "%" + query + "%"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, pattern, limit)
    return [dict(r) for r in rows]


async def healthcheck(pool: asyncpg.Pool) -> bool:
    """Cheap pool-level health probe.

    `SELECT 1` against an existing pooled connection — no fresh
    handshake. The broken baseline opened a new connection per
    /healthz call, which amplifies outages when Postgres is at
    `max_connections`.
    """
    async with pool.acquire() as conn:
        result = await conn.fetchval("SELECT 1")
    return result == 1
