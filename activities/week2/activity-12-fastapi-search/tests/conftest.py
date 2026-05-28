"""
Shared fixtures for the /search test suite.

Postgres-resolution strategy (in priority order):

1. ``DATABASE_URL`` is set and reachable — use it. This is the CI
   path (a GitHub Actions ``services: postgres`` container) and the
   "developer already has a local Postgres" path.
2. ``testcontainers`` + a Docker daemon is available — spin up a
   throwaway Postgres for the session. This is the "developer has
   Docker but no Postgres" path.
3. Neither — every integration test is SKIPPED (not failed), so the
   unit tests still run on a machine with no Postgres and no Docker.

The session-scoped ``pg_pool`` fixture builds the schema, applies the
trigram index migration, and seeds a small dataset so the index is
actually selectable (the planner won't use an index on a 3-row table).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "migrations"
sys.path.insert(0, str(ROOT))

# Enough rows that the planner prefers the trigram index over a seq
# scan. A few thousand is plenty and keeps the fixture fast.
SEED_ROWS = 5000

GENERA = [
    "stegosaurus",
    "tyrannosaurus",
    "velociraptor",
    "triceratops",
    "brachiosaurus",
    "ankylosaurus",
    "diplodocus",
    "pterodactyl",
    "spinosaurus",
    "allosaurus",
]


async def _reachable(dsn: str) -> bool:
    try:
        conn = await asyncpg.connect(dsn, timeout=3)
        await conn.close()
        return True
    except Exception:
        return False


def _start_testcontainer() -> tuple[str, object] | None:
    """Try to start a throwaway Postgres. Returns (dsn, container) or None."""
    try:
        from testcontainers.postgres import PostgresContainer
    except Exception:
        return None
    try:
        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception:
        # Docker daemon not running / not installed.
        return None
    raw = container.get_connection_url()
    # testcontainers hands back a psycopg2 URL; asyncpg wants the plain
    # postgresql:// scheme without the +psycopg2 driver tag.
    dsn = raw.replace("postgresql+psycopg2://", "postgresql://").replace("+psycopg2", "")
    return dsn, container


async def _apply_migrations_and_seed(pool: asyncpg.Pool) -> None:
    schema = (MIGRATIONS / "0000_schema.sql").read_text()
    index = (MIGRATIONS / "0001_search_index.sql").read_text()
    async with pool.acquire() as conn:
        await conn.execute(schema)
        await conn.execute(index)
        await conn.execute("TRUNCATE specimens RESTART IDENTITY")
        rows = [
            (f"{GENERA[i % len(GENERA)]} sp{i}", float(65 + (i % 180)), f"note {i}")
            for i in range(SEED_ROWS)
        ]
        # Session-scoped fixture: this seeds ONCE per test session, so
        # executemany's overhead is a one-time ~2s — fine, and it avoids
        # COPY's binary NUMERIC/Decimal encoding edge cases.
        await conn.executemany(
            "INSERT INTO specimens (species, age_mya, notes) VALUES ($1, $2, $3)",
            rows,
        )
        # ANALYZE so the planner has stats and will pick the index.
        await conn.execute("ANALYZE specimens")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def pg_pool():
    from db import create_pool

    container = None
    dsn = os.environ.get("DATABASE_URL")

    if dsn and await _reachable(dsn):
        pass  # Path 1: use the env DSN.
    else:
        started = _start_testcontainer()  # Path 2: testcontainers.
        if started is None:
            pytest.skip("no Postgres available (set DATABASE_URL or run Docker for testcontainers)")
        dsn, container = started

    pool = await create_pool(dsn)
    try:
        await _apply_migrations_and_seed(pool)
        yield pool
    finally:
        await pool.close()
        if container is not None:
            container.stop()
