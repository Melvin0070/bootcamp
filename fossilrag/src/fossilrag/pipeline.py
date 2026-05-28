"""The walking-skeleton spine: ingest → chunk → embed → store.

One function wires the components so the API handler, the seed script, and
(later) the ingestion Lambda all share exactly one code path. Each stage is
deepened in its own PR; this orchestration contract stays stable.
"""

from __future__ import annotations

from fossilrag.chunking import chunk_document
from fossilrag.embedding.base import Embedder
from fossilrag.ingest import extract_document
from fossilrag.logging import get_logger
from fossilrag.models import IngestResult
from fossilrag.vectorstore.base import VectorStore

log = get_logger("pipeline")


async def ingest_document(
    *,
    store: VectorStore,
    embedder: Embedder,
    filename: str,
    data: bytes,
    content_type: str = "text/plain",
    user_id: str | None = None,
    layer_version: int = 1,
) -> IngestResult:
    """Run raw bytes through the full spine and index the resulting fossils.

    Stages: extract (silver) → chunk (gold) → embed → upsert into the vector
    store. Idempotent end-to-end: identical bytes produce identical ids, so a
    re-run rewrites the same rows rather than duplicating them.
    """
    doc = extract_document(
        filename=filename,
        data=data,
        content_type=content_type,
        user_id=user_id,
    )
    chunks = chunk_document(doc, layer_version=layer_version)
    if not chunks:
        log.info("event=ingest_empty doc_id=%s filename=%s", doc.doc_id[:12], filename)
        return IngestResult(
            doc_id=doc.doc_id,
            filename=filename,
            chunks_total=0,
            chunks_indexed=0,
            layer_version=layer_version,
            embed_model=embedder.model_id,
            embed_dim=embedder.dimensions,
        )

    # CPU-bound; trivial for the mock embedder. PR3's real local embedder runs
    # this in a threadpool to avoid blocking the event loop.
    vectors = embedder.encode([c.content for c in chunks])
    indexed = await store.upsert_chunks(chunks, vectors)

    log.info(
        "event=ingest_complete doc_id=%s filename=%s chunks=%d indexed=%d model=%s dim=%d",
        doc.doc_id[:12],
        filename,
        len(chunks),
        indexed,
        embedder.model_id,
        embedder.dimensions,
    )
    return IngestResult(
        doc_id=doc.doc_id,
        filename=filename,
        chunks_total=len(chunks),
        chunks_indexed=indexed,
        layer_version=layer_version,
        embed_model=embedder.model_id,
        embed_dim=embedder.dimensions,
    )
