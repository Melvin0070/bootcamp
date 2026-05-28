"""Postgres + pgvector backend — the primary, fully-tested vector store.

Design choices (see docs/adr/0001 for the research that fixed them):

* **asyncpg pool** created once and shared, mirroring the activity-12 house
  pattern — no per-request handshake, bounded ``command_timeout``.
* **Code-generated schema.** The embedding column is ``vector(<embed_dim>)``;
  the dimension comes from config, so the same code provisions a 384-dim local
  layer or a 1024-dim Titan layer without editing a frozen ``.sql`` migration.
* **HNSW index with ``vector_cosine_ops``**, queried with the ``<=>`` cosine
  operator. The operator MUST match the opclass or pgvector silently falls
  back to a sequential scan. Recall is tuned per-session via
  ``hnsw.ef_search`` (kept >= the served top-k).
* **Idempotent upsert** keyed on ``(chunk_id, model_id, embed_dim)`` — re-running
  the pipeline rewrites identical rows instead of duplicating fossils, while
  the same content-addressed chunk embedded by a *different* model coexists as
  its own provenance layer rather than overwriting. ``search()`` filters to a
  single ``(model_id, dim)`` space so layers are never cross-queried.

The connection ``init`` ensures the ``vector`` extension exists and registers
the pgvector codec on every pooled connection, so numpy float32 arrays pass
straight through as bind parameters.
"""

from __future__ import annotations

import json
import re
from typing import Any

import asyncpg
import numpy as np
from pgvector.asyncpg import register_vector

from fossilrag.config import Settings
from fossilrag.logging import get_logger
from fossilrag.models import Chunk, EnrichmentRecord, ExcavateHit, FossilLayerChunk, Marker

log = get_logger("vectorstore.pgvector")

# Config-supplied identifiers are interpolated into DDL/DML (you can't bind a
# table name as a parameter), so they must be validated as plain identifiers
# to keep injection impossible by construction.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_ident(name: str, what: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"unsafe {what} identifier: {name!r}")
    return name


class PgVectorStore:
    """pgvector-backed :class:`~fossilrag.vectorstore.base.VectorStore`."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        table: str,
        embed_dim: int,
        model_id: str,
        ef_search: int,
    ) -> None:
        self._pool = pool
        self._table = _safe_ident(table, "table")
        self._dim = int(embed_dim)
        self._model_id = model_id
        self._ef_search = int(ef_search)

    # -- lifecycle --------------------------------------------------------

    @classmethod
    async def connect(cls, settings: Settings) -> PgVectorStore:
        """Create the pool (ensuring the extension + codec) and return a store."""

        async def _init(conn: asyncpg.Connection) -> None:
            # CREATE EXTENSION before register_vector: the codec resolves the
            # 'vector' type OID, which only exists once the extension is
            # installed. IF NOT EXISTS keeps this a cheap no-op per connection.
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await register_vector(conn)

        pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=settings.pool_min_size,
            max_size=settings.pool_max_size,
            command_timeout=settings.command_timeout_sec,
            max_inactive_connection_lifetime=300,
            init=_init,
        )
        log.info(
            "event=pgvector_pool_created min=%d max=%d dim=%d model=%s",
            settings.pool_min_size,
            settings.pool_max_size,
            settings.embed_dim,
            settings.embed_model,
        )
        return cls(
            pool,
            table=settings.vector_table,
            embed_dim=settings.embed_dim,
            model_id=settings.embed_model,
            ef_search=settings.hnsw_ef_search,
        )

    async def bootstrap(self) -> None:
        """Create the chunks table and the HNSW cosine index if absent."""
        ddl = f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                chunk_id       text NOT NULL,
                doc_id         text NOT NULL,
                source_id      text NOT NULL DEFAULT '',
                ordinal        integer NOT NULL,
                content        text NOT NULL,
                layer_version  integer NOT NULL DEFAULT 1,
                geological_age text,
                model_id       text NOT NULL,
                embed_dim      integer NOT NULL,
                metadata       jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                ingested_at    timestamptz NOT NULL DEFAULT now(),
                embedding      vector({self._dim}) NOT NULL,
                -- Composite key enforces (model_id, dim) provenance isolation:
                -- the same content-addressed chunk can coexist as separate
                -- fossils under different embedding models (e.g. mock vs local,
                -- both 384-dim yet incomparable vector spaces) without one
                -- overwriting the other. search() filters to a single space.
                PRIMARY KEY (chunk_id, model_id, embed_dim)
            );
        """
        doc_idx = f"CREATE INDEX IF NOT EXISTS {self._table}_doc_id_idx ON {self._table} (doc_id);"
        layer_idx = (
            f"CREATE INDEX IF NOT EXISTS {self._table}_layer_idx "
            f"ON {self._table} (doc_id, layer_version);"
        )
        # Supports the provenance filter in search()/count() and per-layer ops.
        prov_idx = (
            f"CREATE INDEX IF NOT EXISTS {self._table}_provenance_idx "
            f"ON {self._table} (model_id, embed_dim);"
        )
        # Forward-compat: add source_id to a pre-existing table (CREATE TABLE
        # IF NOT EXISTS can't add a column to an already-created table).
        alter_source = (
            f"ALTER TABLE {self._table} "
            f"ADD COLUMN IF NOT EXISTS source_id text NOT NULL DEFAULT '';"
        )
        # Powers Time-Travel / Fossil Diff: list layers + fetch a layer in order.
        source_idx = (
            f"CREATE INDEX IF NOT EXISTS {self._table}_source_idx "
            f"ON {self._table} (source_id, layer_version, ordinal);"
        )
        # HNSW + cosine. Defaults m=16, ef_construction=64 are fine at this
        # scale; tuned higher only if recall demands it (docs/adr/0001).
        hnsw = (
            f"CREATE INDEX IF NOT EXISTS {self._table}_embedding_hnsw "
            f"ON {self._table} USING hnsw (embedding vector_cosine_ops) "
            f"WITH (m = 16, ef_construction = 64);"
        )
        # Structured enrichment (Automated Enrichment): one markers record per
        # (source_id, layer_version) fossil layer. Model-independent (text
        # extraction, not embeddings), so no provenance columns.
        markers_ddl = f"""
            CREATE TABLE IF NOT EXISTS {self._table}_markers (
                source_id      text NOT NULL,
                layer_version  integer NOT NULL,
                doc_id         text NOT NULL,
                markers        jsonb NOT NULL,
                counts         jsonb NOT NULL,
                created_at     timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (source_id, layer_version)
            );
        """
        async with self._pool.acquire() as conn:
            await conn.execute(ddl)
            await conn.execute(alter_source)
            await conn.execute(doc_idx)
            await conn.execute(layer_idx)
            await conn.execute(prov_idx)
            await conn.execute(source_idx)
            await conn.execute(hnsw)
            await conn.execute(markers_ddl)
        log.info("event=pgvector_bootstrapped table=%s dim=%d", self._table, self._dim)

    async def close(self) -> None:
        await self._pool.close()
        log.info("event=pgvector_pool_closed")

    # -- writes -----------------------------------------------------------

    async def upsert_chunks(self, chunks: list[Chunk], vectors: np.ndarray) -> int:
        """Idempotently upsert chunks + vectors (row-aligned). Returns rows written."""
        if len(chunks) == 0:
            return 0
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[0] != len(chunks):
            raise ValueError(
                f"vectors shape {vectors.shape} does not align with {len(chunks)} chunks"
            )
        if vectors.shape[1] != self._dim:
            raise ValueError(
                f"vector dim {vectors.shape[1]} != store dim {self._dim} "
                f"(model {self._model_id}). Re-embed for this layer."
            )

        rows = [
            (
                c.chunk_id,
                c.doc_id,
                c.ordinal,
                c.content,
                c.layer_version,
                c.geological_age,
                self._model_id,
                self._dim,
                json.dumps(c.metadata),
                c.ingested_at,
                vectors[i],
                c.source_id,
            )
            for i, c in enumerate(chunks)
        ]
        sql = f"""
            INSERT INTO {self._table}
                (chunk_id, doc_id, ordinal, content, layer_version, geological_age,
                 model_id, embed_dim, metadata, ingested_at, embedding, source_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12)
            ON CONFLICT (chunk_id, model_id, embed_dim) DO UPDATE SET
                content        = EXCLUDED.content,
                source_id      = EXCLUDED.source_id,
                layer_version  = EXCLUDED.layer_version,
                geological_age = EXCLUDED.geological_age,
                metadata       = EXCLUDED.metadata,
                ingested_at    = EXCLUDED.ingested_at,
                embedding      = EXCLUDED.embedding;
        """
        async with self._pool.acquire() as conn:
            await conn.executemany(sql, rows)
        log.info(
            "event=chunks_upserted table=%s count=%d model=%s",
            self._table,
            len(rows),
            self._model_id,
        )
        return len(rows)

    # -- reads ------------------------------------------------------------

    async def search(
        self, query_vector: np.ndarray, k: int, source_id: str | None = None
    ) -> list[ExcavateHit]:
        """Top-k cosine-nearest chunks. Score is cosine similarity (1 - distance).

        When ``source_id`` is given, retrieval is scoped to that one logical
        document (Chat-Based Fossil Excavation's "select an existing fossil").
        """
        if k < 1:
            return []
        qv = np.asarray(query_vector, dtype=np.float32).ravel()
        if qv.shape[0] != self._dim:
            raise ValueError(f"query dim {qv.shape[0]} != store dim {self._dim}")

        # Provenance isolation: only ever search this store's own
        # (model_id, dim) vector space (two models can share a dim, e.g. mock
        # vs all-MiniLM at 384). Param numbering is built dynamically so the
        # optional source_id filter slots in before the LIMIT placeholder.
        where = "WHERE model_id = $2 AND embed_dim = $3"
        args: list = [qv, self._model_id, self._dim]
        if source_id is not None:
            args.append(source_id)
            where += f" AND source_id = ${len(args)}"
        args.append(k)
        limit_ph = f"${len(args)}"
        sql = f"""
            SELECT chunk_id, doc_id, source_id, ordinal, content, layer_version,
                   geological_age, metadata,
                   1 - (embedding <=> $1) AS score
            FROM {self._table}
            {where}
            ORDER BY embedding <=> $1
            LIMIT {limit_ph};
        """
        ef = max(self._ef_search, k)  # keep ef_search >= k
        async with self._pool.acquire() as conn, conn.transaction():
            # SET LOCAL scopes these to the txn so pooled connections don't leak
            # the settings; relaxed-order iterative scan avoids overfiltering the
            # filtered ANN query (pgvector 0.8+).
            await conn.execute(f"SET LOCAL hnsw.ef_search = {ef}")
            await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            records = await conn.fetch(sql, *args)

        return [
            ExcavateHit(
                chunk_id=r["chunk_id"],
                doc_id=r["doc_id"],
                source_id=r["source_id"],
                ordinal=r["ordinal"],
                content=r["content"],
                score=float(r["score"]),
                layer_version=r["layer_version"],
                geological_age=r["geological_age"],
                metadata=json.loads(r["metadata"]) if r["metadata"] else {},
            )
            for r in records
        ]

    async def count(self) -> int:
        """Row count for THIS store's (model_id, embed_dim) provenance layer."""
        async with self._pool.acquire() as conn:
            return int(
                await conn.fetchval(
                    f"SELECT count(*) FROM {self._table} WHERE model_id = $1 AND embed_dim = $2",
                    self._model_id,
                    self._dim,
                )
            )

    # -- fossil layers (Time-Travel / Diff) -------------------------------

    async def list_layers(self, source_id: str) -> list[int]:
        """Ascending list of fossil-layer versions for a logical document."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT DISTINCT layer_version FROM {self._table} "
                f"WHERE source_id = $1 AND model_id = $2 AND embed_dim = $3 "
                f"ORDER BY layer_version",
                source_id,
                self._model_id,
                self._dim,
            )
        return [int(r["layer_version"]) for r in rows]

    async def latest_layer(self, source_id: str) -> int | None:
        layers = await self.list_layers(source_id)
        return layers[-1] if layers else None

    async def get_layer(self, source_id: str, layer_version: int) -> list[FossilLayerChunk]:
        """All chunks of one fossil layer, in document order (no embedding)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT chunk_id, doc_id, source_id, ordinal, content, layer_version, "
                f"geological_age FROM {self._table} "
                f"WHERE source_id = $1 AND layer_version = $2 "
                f"AND model_id = $3 AND embed_dim = $4 "
                f"ORDER BY ordinal",
                source_id,
                layer_version,
                self._model_id,
                self._dim,
            )
        return [
            FossilLayerChunk(
                chunk_id=r["chunk_id"],
                doc_id=r["doc_id"],
                source_id=r["source_id"],
                ordinal=r["ordinal"],
                content=r["content"],
                layer_version=r["layer_version"],
                geological_age=r["geological_age"],
            )
            for r in rows
        ]

    # -- structured enrichment (Automated Enrichment) ---------------------

    async def store_markers(
        self, *, doc_id: str, source_id: str, layer_version: int, markers: list[Marker]
    ) -> EnrichmentRecord:
        """Upsert a layer's extracted markers as a structured enrichment record."""
        counts: dict[str, int] = {}
        for m in markers:
            counts[m.kind] = counts.get(m.kind, 0) + 1
        payload = json.dumps([m.model_dump() for m in markers])
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self._table}_markers
                    (source_id, layer_version, doc_id, markers, counts)
                VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)
                ON CONFLICT (source_id, layer_version) DO UPDATE SET
                    doc_id = EXCLUDED.doc_id,
                    markers = EXCLUDED.markers,
                    counts = EXCLUDED.counts,
                    created_at = now();
                """,
                source_id,
                layer_version,
                doc_id,
                payload,
                json.dumps(counts),
            )
        log.info(
            "event=markers_stored source_id=%s layer=%d count=%d",
            source_id,
            layer_version,
            len(markers),
        )
        return EnrichmentRecord(
            source_id=source_id,
            doc_id=doc_id,
            layer_version=layer_version,
            markers=markers,
            counts=counts,
        )

    async def get_markers(self, source_id: str, layer_version: int) -> EnrichmentRecord | None:
        """Fetch a layer's stored enrichment record, or None if absent."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT doc_id, markers, counts FROM {self._table}_markers "
                f"WHERE source_id = $1 AND layer_version = $2",
                source_id,
                layer_version,
            )
        if row is None:
            return None
        return EnrichmentRecord(
            source_id=source_id,
            doc_id=row["doc_id"],
            layer_version=layer_version,
            markers=[Marker(**m) for m in json.loads(row["markers"])],
            counts=json.loads(row["counts"]),
        )

    async def healthcheck(self) -> bool:
        async with self._pool.acquire() as conn:
            return (await conn.fetchval("SELECT 1")) == 1

    def stats(self) -> dict[str, Any]:
        return {
            "backend": "pgvector",
            "table": self._table,
            "embed_dim": self._dim,
            "model_id": self._model_id,
            "hnsw_ef_search": self._ef_search,
            "pool_size": self._pool.get_size(),
            "pool_free": self._pool.get_idle_size(),
        }
