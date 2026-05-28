"""
Integration tests against a real Postgres (via DATABASE_URL or
testcontainers). Skipped automatically when neither is available.

These are the tests that prove the acceptance criteria:
  - the endpoint handles concurrent requests without blocking
  - the trigram index is actually used (EXPLAIN)
  - parameterised queries close the injection hole
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import healthcheck, search_specimens  # noqa: E402

# Session loop scope so these tests share the same event loop as the
# session-scoped pg_pool fixture — asyncpg pools are loop-bound.
pytestmark = pytest.mark.asyncio(loop_scope="session")


class TestSearch:
    async def test_returns_matching_species(self, pg_pool):
        rows = await search_specimens(pg_pool, "stego", 10)
        assert len(rows) > 0
        assert all("stego" in r["species"].lower() for r in rows)

    async def test_limit_is_respected(self, pg_pool):
        rows = await search_specimens(pg_pool, "saurus", 5)
        assert len(rows) <= 5

    async def test_no_match_returns_empty(self, pg_pool):
        rows = await search_specimens(pg_pool, "zzzznotaspecies", 10)
        assert rows == []

    async def test_rows_have_expected_columns(self, pg_pool):
        rows = await search_specimens(pg_pool, "trex", 1)
        if rows:
            assert set(rows[0].keys()) == {"id", "species", "age_mya", "notes"}


class TestInjectionSafety:
    async def test_injection_payload_is_treated_as_data(self, pg_pool):
        # A classic injection payload. With the parameterised query it
        # is bound as a literal string and matches nothing — it does
        # NOT drop the table or return all rows.
        payload = "'; DROP TABLE specimens; --"
        rows = await search_specimens(pg_pool, payload, 10)
        assert rows == []
        # Table still exists and still has data.
        sanity = await search_specimens(pg_pool, "stego", 1)
        assert len(sanity) == 1


class TestIndexUsage:
    async def test_explain_uses_trigram_index(self, pg_pool):
        """Acceptance criterion: the index exists AND is used.

        The fixture ANALYZEs the table so the planner has real stats.
        We use a selective pattern (one matching row) so the index
        scan is unambiguously cheaper than a seq scan, then assert the
        plan names the trigram index and contains no sequential scan.
        """
        async with pg_pool.acquire() as conn:
            plan_rows = await conn.fetch(
                "EXPLAIN (FORMAT TEXT) "
                "SELECT id, species, age_mya, notes FROM specimens "
                "WHERE species ILIKE $1 ORDER BY species LIMIT $2",
                "%sp4999%",
                10,
            )
        plan = "\n".join(r[0] for r in plan_rows)
        assert "specimens_species_trgm_idx" in plan, plan
        assert "Seq Scan" not in plan, plan


class TestConcurrency:
    async def test_many_concurrent_queries_do_not_block(self, pg_pool):
        """50 concurrent searches complete quickly because the pool +
        async I/O let them overlap. On the broken (sync, no-pool)
        baseline this would serialise and/or exhaust connections."""

        async def one():
            return await search_specimens(pg_pool, "saurus", 10)

        t0 = time.perf_counter()
        results = await asyncio.gather(*[one() for _ in range(50)])
        elapsed = time.perf_counter() - t0

        assert len(results) == 50
        assert all(isinstance(r, list) for r in results)
        # 50 indexed queries through a 20-connection pool should be
        # well under a second even on a slow CI runner. Generous bound
        # to avoid flakes; the point is "not 50× serial latency."
        assert elapsed < 5.0, f"50 concurrent searches took {elapsed:.2f}s"

    async def test_pool_size_is_bounded(self, pg_pool):
        # The pool must not open an unbounded number of connections.
        assert pg_pool.get_max_size() <= 20


class TestHealthcheck:
    async def test_healthcheck_returns_true(self, pg_pool):
        assert await healthcheck(pg_pool) is True


class TestAppEndToEnd:
    """Drive the FastAPI app with the real pool injected, so the
    handler happy-path (validation → db → 200) is covered, not just
    the 422/503 guard paths the unit tests exercise.

    Uses httpx.AsyncClient + ASGITransport (NOT the sync TestClient):
    asyncpg pools are bound to the event loop they were created on, and
    the sync TestClient would run the app on a different loop in a
    portal thread, crashing on pool.acquire(). The async client runs
    the handler on the same loop as the session-scoped pg_pool.
    """

    @staticmethod
    def _client():
        import httpx

        import app as app_module

        transport = httpx.ASGITransport(app=app_module.app)
        return httpx.AsyncClient(transport=transport, base_url="http://test"), app_module

    async def test_search_endpoint_returns_results(self, pg_pool):
        client, app_module = self._client()
        app_module._POOL = pg_pool
        async with client:
            resp = await client.get("/search?q=stego&limit=5")
        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] == "stego"
        assert len(body["results"]) > 0
        assert "latency_ms" in body

    async def test_search_injection_payload_returns_empty(self, pg_pool):
        client, app_module = self._client()
        app_module._POOL = pg_pool
        async with client:
            resp = await client.get("/search", params={"q": "'; DROP TABLE specimens; --"})
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    async def test_healthz_endpoint_ok_with_pool(self, pg_pool):
        client, app_module = self._client()
        app_module._POOL = pg_pool
        async with client:
            resp = await client.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "pool_size" in body
