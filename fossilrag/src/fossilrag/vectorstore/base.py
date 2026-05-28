"""The VectorStore interface.

Async because the primary backend (pgvector via asyncpg) is async and the API
is async end-to-end. Every store is keyed to a single (model_id, dim) vector
space; mixing spaces in one store is a caller error, not something the
interface tries to reconcile.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import numpy as np

from fossilrag.models import Chunk, ExcavateHit


@runtime_checkable
class VectorStore(Protocol):
    """Persist embedded chunks and serve top-k similarity search."""

    async def bootstrap(self) -> None:
        """Create schema/index if absent. Idempotent."""
        ...

    async def upsert_chunks(self, chunks: list[Chunk], vectors: np.ndarray) -> int:
        """Insert/update ``chunks`` with their ``vectors`` (row-aligned).

        Idempotent on ``chunk_id``; returns the number of rows written.
        """
        ...

    async def search(self, query_vector: np.ndarray, k: int) -> list[ExcavateHit]:
        """Return the ``k`` nearest chunks to ``query_vector`` by cosine similarity."""
        ...

    async def healthcheck(self) -> bool:
        """Cheap liveness probe."""
        ...

    async def close(self) -> None:
        """Release resources (connection pool, client)."""
        ...

    def stats(self) -> dict[str, Any]:
        """Operational metadata for ``/healthz`` and dashboards."""
        ...
