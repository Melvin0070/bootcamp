"""Integration + e2e tests — need a live Postgres+pgvector (CI service container).

Skipped locally (no ``DATABASE_URL``). Heavy imports (asyncpg via the pgvector
store, the FastAPI app) are done INSIDE each test so local collection never
imports asyncpg, which has no wheel on this dev box.
"""

from __future__ import annotations

import uuid

import numpy as np
import pytest

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]


async def _build_chunks(doc_id, texts):
    from fossilrag.models import Chunk, compute_chunk_id, geological_age_for

    return [
        Chunk(
            chunk_id=compute_chunk_id(doc_id, i, t),
            doc_id=doc_id,
            ordinal=i,
            content=t,
            char_count=len(t),
            layer_version=1,
            geological_age=geological_age_for(1),
            metadata={"filename": "test.txt"},
        )
        for i, t in enumerate(texts)
    ]


async def test_pgvector_roundtrip_and_idempotency():
    from fossilrag.config import Settings
    from fossilrag.embedding.mock import MockEmbedder
    from fossilrag.vectorstore.pgvector import PgVectorStore

    table = "test_fossils_" + uuid.uuid4().hex[:8]
    settings = Settings(
        embed_provider="mock", embed_model="mock-test", embed_dim=48, vector_table=table
    )
    embedder = MockEmbedder(model_id="mock-test", dimensions=48)
    store = await PgVectorStore.connect(settings)
    try:
        await store.bootstrap()
        texts = ["alpha allosaurus", "beta brontosaurus", "gamma gallimimus"]
        chunks = await _build_chunks("doc-test", texts)
        vectors = embedder.encode(texts)

        assert await store.upsert_chunks(chunks, vectors) == 3
        assert await store.count() == 3

        # The exact text of a chunk retrieves that chunk first, at ~1.0 cosine.
        hits = await store.search(embedder.encode_one("beta brontosaurus"), k=3)
        assert hits[0].content == "beta brontosaurus"
        assert hits[0].score > 0.99
        assert hits[0].geological_age == "Holocene"
        assert hits[0].metadata["filename"] == "test.txt"

        # Re-upserting identical content-addressed chunks is a no-op on count.
        assert await store.upsert_chunks(chunks, vectors) == 3
        assert await store.count() == 3
    finally:
        async with store._pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS {table}")
        await store.close()


async def test_upsert_rejects_dim_mismatch():
    from fossilrag.config import Settings
    from fossilrag.vectorstore.pgvector import PgVectorStore

    table = "test_fossils_" + uuid.uuid4().hex[:8]
    settings = Settings(embed_provider="mock", embed_dim=16, vector_table=table)
    store = await PgVectorStore.connect(settings)
    try:
        await store.bootstrap()
        chunks = await _build_chunks("doc-x", ["one", "two"])
        wrong = np.zeros((2, 8), dtype=np.float32)  # store expects dim 16
        with pytest.raises(ValueError):
            await store.upsert_chunks(chunks, wrong)
    finally:
        async with store._pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS {table}")
        await store.close()


async def test_provenance_isolation_same_dim():
    """Two different models at the SAME dim in the SAME table must not mix.

    mock and all-MiniLM are both 384-dim yet incomparable spaces, so this is
    the real hazard. Composite key (chunk_id, model_id, embed_dim) prevents
    overwrite; the provenance filter in search() prevents cross-model hits.
    """
    from fossilrag.config import Settings
    from fossilrag.embedding.mock import MockEmbedder
    from fossilrag.vectorstore.pgvector import PgVectorStore

    table = "test_fossils_" + uuid.uuid4().hex[:8]
    dim = 32
    texts = ["alpha", "beta", "gamma"]
    chunks = await _build_chunks("shared-doc", texts)  # identical chunk_ids for both

    s_a = Settings(embed_model="model-A", embed_dim=dim, vector_table=table)
    s_b = Settings(embed_model="model-B", embed_dim=dim, vector_table=table)
    store_a = await PgVectorStore.connect(s_a)
    store_b = await PgVectorStore.connect(s_b)
    try:
        await store_a.bootstrap()
        await store_b.bootstrap()  # idempotent; same physical table

        await store_a.upsert_chunks(chunks, MockEmbedder("model-A", dim).encode(texts))
        await store_b.upsert_chunks(chunks, MockEmbedder("model-B", dim).encode(texts))

        # No overwrite: each provenance layer keeps its own 3 rows (6 total).
        assert await store_a.count() == 3
        assert await store_b.count() == 3

        # Isolated search: store_a sees only model-A's rows (3, not 6) even
        # with k=10, despite identical chunk_ids/vectors in model-B's layer.
        hits = await store_a.search(MockEmbedder("model-A", dim).encode_one("beta"), k=10)
        assert len(hits) == 3
        assert {h.chunk_id for h in hits} == {c.chunk_id for c in chunks}
    finally:
        async with store_a._pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS {table}")
        await store_a.close()
        await store_b.close()


def test_excavate_endpoint_e2e():
    """Full HTTP spine through the FastAPI app, including top-k ranking.

    Six paragraphs are ingested and k=3 is requested, so the store must
    *rank* rather than return everything. The query is the EXACT text of one
    chunk, which (with the deterministic mock embedder) self-matches at
    cosine ~1.0 — so the relevant fossil must come back first. This exercises
    HNSW cosine ordering through the real HTTP path, deterministically.
    """
    from fastapi.testclient import TestClient

    from fossilrag.api.app import app

    spike_para = "Its tail bore four sharp spikes, a feature palaeontologists call a thagomizer."
    text = "\n\n".join(
        [
            "Stegosaurus had two rows of distinctive bony plates along its back.",
            "It lived during the Late Jurassic period in what is now North America.",
            spike_para,
            "Its brain was famously small relative to its large body.",
            "The plates may have been used for display or thermoregulation.",
            "Adults could reach roughly nine metres in length.",
        ]
    )
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/").json()["service"] == "fossilrag"

        r = client.post("/ingest", json={"filename": "stego.txt", "text": text})
        assert r.status_code == 200, r.text
        ingested = r.json()
        assert ingested["chunks_indexed"] == 6

        # Query the exact text of one chunk: it must rank first (k<rows, so
        # ordering is load-bearing) at near-perfect self-similarity.
        r = client.get("/excavate", params={"q": spike_para, "k": 3})
        assert r.status_code == 200
        hits = r.json()["hits"]
        assert len(hits) == 3
        assert "thagomizer" in hits[0]["content"]
        assert hits[0]["score"] > 0.99

        # /mutate (mock) retrieves and grounds a summary in the same fossils.
        m = client.post("/mutate", json={"query": spike_para, "k": 3, "instruction": "be concise"})
        assert m.status_code == 200
        mr = m.json()
        assert mr["mock"] is True
        assert len(mr["used_chunks"]) == 3
        assert "thagomizer" in mr["summary"]

        # Unsupported content type → clean 422, not a 500.
        bad = client.post(
            "/ingest",
            json={"filename": "x.zip", "text": "x", "content_type": "application/zip"},
        )
        assert bad.status_code == 422
