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
    import os

    from fastapi.testclient import TestClient

    from fossilrag.api.app import app
    from fossilrag.config import get_settings

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
    # Force tiny chunks so the six single-sentence paragraphs become six chunks
    # (the default 256-token budget would pack them into one), keeping the
    # top-k ranking assertion meaningful. overlap=0 keeps each chunk = one
    # sentence, so the exact-text query self-matches at cosine ~1.0.
    keys = ("FOSSILRAG_CHUNK_MAX_TOKENS", "FOSSILRAG_CHUNK_OVERLAP_TOKENS")
    prev = {k: os.environ.get(k) for k in keys}
    os.environ["FOSSILRAG_CHUNK_MAX_TOKENS"] = "16"  # config min; one sentence/chunk here
    os.environ["FOSSILRAG_CHUNK_OVERLAP_TOKENS"] = "0"
    get_settings.cache_clear()
    try:
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

            # /mutate (mock) + Prompt Fossilization: first call computes, the
            # second identical call is served from the cache instantly.
            payload = {"query": spike_para, "k": 3, "instruction": "be concise"}
            mr1 = client.post("/mutate", json=payload).json()
            assert mr1["mock"] is True and mr1["cached"] is False
            assert mr1["model_id"] == "mock-llm-v1"
            assert len(mr1["used_chunks"]) == 3
            assert "thagomizer" in mr1["summary"]
            mr2 = client.post("/mutate", json=payload).json()
            assert mr2["cached"] is True  # Prompt Fossilization hit
            assert mr2["summary"] == mr1["summary"]

            # Unsupported content type → clean 422, not a 500.
            bad = client.post(
                "/ingest",
                json={"filename": "x.zip", "text": "x", "content_type": "application/zip"},
            )
            assert bad.status_code == 422
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        get_settings.cache_clear()


def test_timetravel_and_diff_e2e():
    """Ingest two versions under one source_id; time-travel + diff over HTTP."""
    from fastapi.testclient import TestClient

    from fossilrag.api.app import app

    sid = "report-timetravel"
    with TestClient(app) as client:
        client.post(
            "/ingest",
            json={
                "filename": "r.txt",
                "text": "Alpha fact. Beta fact.",
                "source_id": sid,
                "layer_version": 1,
            },
        ).raise_for_status()
        client.post(
            "/ingest",
            json={
                "filename": "r.txt",
                "text": "Alpha fact. Gamma fact. Delta fact.",
                "source_id": sid,
                "layer_version": 2,
            },
        ).raise_for_status()

        # Time-Travel: explicit older layer + default (latest).
        tt = client.get("/timetravel", params={"source_id": sid, "version": 1}).json()
        assert tt["available_versions"] == [1, 2]
        assert tt["version"] == 1 and tt["latest_version"] == 2 and tt["is_latest"] is False
        assert tt["chunks"] and all(c["layer_version"] == 1 for c in tt["chunks"])
        latest = client.get("/timetravel", params={"source_id": sid}).json()
        assert latest["version"] == 2 and latest["is_latest"] is True

        # Fossil Diff between the two layers.
        df = client.get(
            "/diff", params={"source_id": sid, "from_version": 1, "to_version": 2}
        ).json()
        assert df["changed"] is True
        assert df["from_version"] == 1 and df["to_version"] == 2
        assert df["unified_diff"]

        # 404s for unknown source / version.
        assert client.get("/timetravel", params={"source_id": "nope"}).status_code == 404
        assert (
            client.get(
                "/diff", params={"source_id": sid, "from_version": 1, "to_version": 9}
            ).status_code
            == 404
        )


def test_enrich_and_markers_e2e():
    """Automated Enrichment over HTTP: extract markers, persist, retrieve."""
    from fastapi.testclient import TestClient

    from fossilrag.api.app import app

    sid = "logs-enrich"
    log = "2026-05-29 ERROR HTTP 503 after 1200ms; retries=3. Raised ConnectionError. ERR-4521."
    with TestClient(app) as client:
        r = client.post(
            "/enrich",
            json={"filename": "app.log", "text": log, "source_id": sid, "layer_version": 1},
        )
        assert r.status_code == 200, r.text
        rec = r.json()
        assert rec["source_id"] == sid and rec["layer_version"] == 1
        assert rec["counts"].get("error_code", 0) >= 2
        texts = {m["text"] for m in rec["markers"]}
        assert "2026-05-29" in texts and "ERR-4521" in texts and "1200ms" in texts

        got = client.get("/markers", params={"source_id": sid, "version": 1})
        assert got.status_code == 200
        assert got.json()["counts"] == rec["counts"]

        assert client.get("/markers", params={"source_id": "nope", "version": 1}).status_code == 404


def test_chat_e2e():
    """Chat-Based Fossil Excavation: grounded answer + citations + cache + 422."""
    from fastapi.testclient import TestClient

    from fossilrag.api.app import app

    with TestClient(app) as client:
        client.post(
            "/ingest",
            json={
                "filename": "chatdoc.txt",
                "text": "Stegosaurus had two rows of bony plates. It lived in the Late Jurassic.",
                "source_id": "chatdoc",
            },
        ).raise_for_status()

        body = {
            "messages": [{"role": "user", "content": "Tell me about the plates"}],
            "k": 3,
            "source_id": "chatdoc",
        }
        r = client.post("/chat", json=body)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["mock"] is True and j["cached"] is False
        assert j["model_id"] == "mock-llm-v1"
        assert j["citations"]  # grounded in retrieved fossils
        assert "geological_age" in j["citations"][0]
        assert all(c["source_id"] == "chatdoc" for c in j["citations"])  # source-scoped

        # Prompt Fossilization: identical conversation → cache hit.
        assert client.post("/chat", json=body).json()["cached"] is True

        # No user message → 422.
        bad = client.post("/chat", json={"messages": [{"role": "assistant", "content": "hi"}]})
        assert bad.status_code == 422

        # Source-scoped /excavate returns only this document's fossils.
        ex = client.get("/excavate", params={"q": "plates", "k": 5, "source_id": "chatdoc"})
        assert ex.status_code == 200
        assert ex.json()["hits"] and all(h["source_id"] == "chatdoc" for h in ex.json()["hits"])
