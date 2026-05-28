"""Idempotent batch embedding: chunks → (skip-or-embed) → vector store.

The Self-Healing Idempotency path. For each chunk:

  1. ``is_processed`` for this ``(model_id, dim, chunk_id)`` → skip (no embed).
  2. else ``claim`` it (atomic) — if another worker holds it, skip.
  3. batch-embed claimed chunks, upsert to the vector store, ``mark_processed``.
  4. on any embed/upsert failure, ``release`` the claims so the next run retries.

Re-running over an already-indexed corpus is therefore ~free (every chunk is
skipped before the expensive embed call), and a crash mid-batch self-heals.
"""

from __future__ import annotations

from dataclasses import dataclass

from fossilrag.embedding.base import Embedder
from fossilrag.idempotency import IdempotencyStore, NullStore
from fossilrag.logging import get_logger
from fossilrag.models import Chunk
from fossilrag.vectorstore.base import VectorStore

log = get_logger("embedding.pipeline")


@dataclass(frozen=True)
class EmbedResult:
    total: int = 0
    embedded: int = 0
    skipped_processed: int = 0
    skipped_locked: int = 0
    failed: int = 0


async def embed_chunks(
    chunks: list[Chunk],
    *,
    store: VectorStore,
    embedder: Embedder,
    idempotency: IdempotencyStore | None = None,
    batch_size: int = 64,
) -> EmbedResult:
    """Idempotently embed + index ``chunks``. Returns per-stage counts."""
    idem = idempotency or NullStore()

    def key(chunk_id: str) -> str:
        return f"{embedder.model_id}:{embedder.dimensions}:{chunk_id}"

    total = skipped_processed = skipped_locked = embedded = failed = 0
    pending: list[Chunk] = []

    async def flush() -> None:
        nonlocal embedded, failed
        if not pending:
            return
        batch = list(pending)
        pending.clear()
        try:
            vectors = embedder.encode([c.content for c in batch])
            await store.upsert_chunks(batch, vectors)
        except Exception:
            log.exception("event=embed_batch_failed size=%d", len(batch))
            for c in batch:
                idem.release(key(c.chunk_id))
            failed += len(batch)
            return
        for c in batch:
            idem.mark_processed(key(c.chunk_id), f"vector:{c.chunk_id}")
        embedded += len(batch)

    for chunk in chunks:
        total += 1
        k = key(chunk.chunk_id)
        if idem.is_processed(k):
            skipped_processed += 1
            continue
        if not idem.claim(k):
            skipped_locked += 1
            continue
        pending.append(chunk)
        if len(pending) >= batch_size:
            await flush()
    await flush()

    log.info(
        "event=embed_complete total=%d embedded=%d skipped_processed=%d skipped_locked=%d failed=%d",
        total,
        embedded,
        skipped_processed,
        skipped_locked,
        failed,
    )
    return EmbedResult(
        total=total,
        embedded=embedded,
        skipped_processed=skipped_processed,
        skipped_locked=skipped_locked,
        failed=failed,
    )
