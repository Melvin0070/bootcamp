"""Shared test config.

Unit tests run anywhere (no DB). Integration/e2e tests need a live
Postgres+pgvector and are gated on ``DATABASE_URL`` (set by the CI service
container); locally they skip. Heavy imports (asyncpg, the API app) live
inside the integration tests so local collection never imports them.
"""

from __future__ import annotations

import os

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("FOSSILRAG_DATABASE_URL")

# Reusable skip guard for integration tests.
requires_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="no DATABASE_URL — integration/e2e tests run in CI against a pgvector service",
)
