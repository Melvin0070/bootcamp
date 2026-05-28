"""
Unit tests that need NO database.

Covers:
  - the SQL query shape (parameterised, no f-string interpolation)
  - pool config defaults
  - FastAPI request validation + pool-not-ready guard (via TestClient,
    pool mocked out)
  - broken-baseline regression (the anti-patterns must stay present so
    the before/after is demonstrable)
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db  # noqa: E402

# ---------------------------------------------------------------------------
# SQL shape — the fix's core safety properties, assertable without a DB.
# ---------------------------------------------------------------------------


class TestQueryShape:
    def test_search_uses_parameterised_placeholders(self):
        src = inspect.getsource(db.search_specimens)
        # asyncpg positional params.
        assert "$1" in src
        assert "$2" in src

    def test_search_does_not_f_string_the_query(self):
        src = inspect.getsource(db.search_specimens)
        # No f-string SQL anywhere — that was the injection vector.
        assert 'f"' not in src
        assert "f'" not in src
        # The wildcards live in the bound value, not literal in the SQL.
        assert "ILIKE '%" not in src

    def test_search_uses_indexed_ilike(self):
        src = inspect.getsource(db.search_specimens)
        # Substring match via ILIKE on a parameter — pg_trgm GIN index
        # accelerates this (Bitmap Index Scan), unlike a btree.
        assert "ILIKE $1" in src
        # Pattern is built by concatenation of bound value, not f-string.
        assert 'pattern = "%" + query + "%"' in src

    def test_healthcheck_uses_select_1(self):
        src = inspect.getsource(db.healthcheck)
        assert "SELECT 1" in src


class TestPoolConfig:
    def test_pool_has_bounded_size_defaults(self):
        assert db.POOL_MAX_SIZE <= 20
        assert db.POOL_MIN_SIZE >= 1
        assert db.POOL_MIN_SIZE <= db.POOL_MAX_SIZE

    def test_command_timeout_is_set(self):
        assert db.COMMAND_TIMEOUT_SEC > 0

    async def test_create_pool_raises_without_dsn(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        import pytest

        with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
            await db.create_pool(None)


# ---------------------------------------------------------------------------
# FastAPI validation — pool is never touched because validation fails
# first (422) or the pool guard returns 503 before any DB call.
# ---------------------------------------------------------------------------


class TestValidation:
    def setup_method(self):
        import app as app_module

        # Ensure the pool guard path is exercised (no real DB).
        app_module._POOL = None
        # Build a TestClient WITHOUT triggering lifespan (which would
        # try to open a real pool). We hit the routes directly.
        self.app_module = app_module
        self.client = TestClient(app_module.app, raise_server_exceptions=False)

    def test_empty_query_is_rejected_422(self):
        # min_length=1 → empty string fails validation before any DB work.
        resp = self.client.get("/search?q=")
        assert resp.status_code == 422

    def test_missing_query_is_rejected_422(self):
        resp = self.client.get("/search")
        assert resp.status_code == 422

    def test_limit_above_max_is_rejected_422(self):
        resp = self.client.get("/search?q=trex&limit=1000")
        assert resp.status_code == 422

    def test_limit_below_min_is_rejected_422(self):
        resp = self.client.get("/search?q=trex&limit=0")
        assert resp.status_code == 422

    def test_valid_query_without_pool_returns_503(self):
        # Validation passes, but the pool isn't ready → 503, not a crash.
        resp = self.client.get("/search?q=trex&limit=10")
        assert resp.status_code == 503

    def test_healthz_without_pool_returns_503(self):
        resp = self.client.get("/healthz")
        assert resp.status_code == 503


class TestErrorBranches:
    """Cover the defensive except branches: a DB error mid-query and an
    unhealthy pool must surface as 500, not an unhandled crash."""

    def test_search_db_error_returns_500(self, monkeypatch):
        import asyncpg

        import app as app_module

        # A non-None sentinel pool so the guard passes; the failure is
        # injected at the query layer instead.
        app_module._POOL = object()

        async def boom(*_a, **_k):
            raise asyncpg.exceptions.PostgresError("simulated db failure")

        monkeypatch.setattr(app_module, "search_specimens", boom)
        client = TestClient(app_module.app, raise_server_exceptions=False)
        resp = client.get("/search?q=trex&limit=5")
        assert resp.status_code == 500

    def test_healthz_unhealthy_pool_returns_500(self, monkeypatch):
        import app as app_module

        app_module._POOL = object()

        async def boom(*_a, **_k):
            raise RuntimeError("pool exploded")

        monkeypatch.setattr(app_module, "healthcheck", boom)
        client = TestClient(app_module.app, raise_server_exceptions=False)
        resp = client.get("/healthz")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Broken-baseline regression — the anti-patterns must remain so the
# before/after stays demonstrable.
# ---------------------------------------------------------------------------


class TestBrokenBaseline:
    BROKEN = ROOT / "broken" / "app.py"

    def test_broken_opens_connection_per_request(self):
        src = self.BROKEN.read_text()
        assert "psycopg2.connect" in src
        # No pool anywhere in the broken file — no create_pool, no
        # acquire(). Each request does a fresh connect.
        assert "create_pool" not in src
        assert ".acquire(" not in src

    def test_broken_is_synchronous(self):
        src = self.BROKEN.read_text()
        # The broken search handler is a plain def, not async def.
        assert "def search(" in src
        assert "async def search(" not in src

    def test_broken_has_sql_injection(self):
        src = self.BROKEN.read_text()
        # f-string interpolation of the query into the SQL.
        assert "ILIKE '%{q}%'" in src

    def test_broken_has_no_index_friendly_query(self):
        src = self.BROKEN.read_text()
        # Leading-wildcard ILIKE cannot use a btree index.
        assert "ILIKE '%" in src

    def test_fixed_app_is_async(self):
        src = (ROOT / "app.py").read_text()
        assert "async def search(" in src
        assert "await search_specimens" in src
