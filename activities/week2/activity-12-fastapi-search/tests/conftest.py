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
trigram index migration, and seeds enough rows (SEED_ROWS) that the
planner genuinely prefers the index for a selective query — on a tiny
table a sequential scan is cheaper and the index would look "unused."
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

# Enough rows that the planner genuinely prefers the GIN trigram index
# over a sequential scan for a selective match. On a tiny table a seq
# scan is cheaper, so the index would look "unused" — a fixture-size
# artefact, not a bug. Seeded server-side via generate_series, so even
# 200k rows is ~1s; at this size a selective ILIKE makes the index path
# clearly cheaper than a full scan, so the planner picks it.
SEED_ROWS = 200_000

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
    genera_array = "ARRAY[" + ",".join(f"'{g}'" for g in GENERA) + "]"
    async with pool.acquire() as conn:
        await conn.execute(schema)
        await conn.execute(index)
        await conn.execute("TRUNCATE specimens RESTART IDENTITY")
        # Seed server-side with generate_series — orders of magnitude
        # faster than executemany, and big enough (SEED_ROWS) that the
        # planner genuinely prefers the GIN index over a seq scan. On a
        # tiny table a seq scan is cheaper, so the index would look
        # "unused" even though it's correct (that's a fixture-size
        # artefact, not a bug — see docs/explain-output.md).
        await conn.execute(
            f"""
            INSERT INTO specimens (species, age_mya, notes)
            SELECT
                ({genera_array})[(g % {len(GENERA)}) + 1] || ' sp' || g,
                (65 + (g % 180))::numeric,
                'note ' || g
            FROM generate_series(0, {SEED_ROWS - 1}) AS g
            """
        )
        # ANALYZE so the planner has stats and will pick the index.
        await conn.execute("ANALYZE specimens")
        # Fail loud + clear if the migration didn't actually create the
        # index, instead of letting a later EXPLAIN assertion fail with a
        # confusing "Seq Scan" message.
        idx = await conn.fetchval(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'specimens_species_trgm_idx'"
        )
        if idx != 1:
            raise RuntimeError("specimens_species_trgm_idx was not created — check migrations/0001")


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
        # Expose the DSN via the env so the app's lifespan (which calls
        # create_pool() with no args) works in the testcontainers path,
        # matching the CI service-container path where DATABASE_URL is set.
        os.environ["DATABASE_URL"] = dsn

    pool = await create_pool(dsn)
    try:
        await _apply_migrations_and_seed(pool)
        yield pool
    finally:
        await pool.close()
        if container is not None:
            container.stop()
