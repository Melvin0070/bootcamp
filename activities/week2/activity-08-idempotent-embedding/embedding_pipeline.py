"""
Embedding generation pipeline — production-grade, idempotent.

Key changes from `broken/embedding_pipeline.py`:

1. Content-addressed chunk ids — `chunk_id = sha256(text + metadata)` so
   re-running over the same corpus is a no-op, and the same paragraph in
   two documents produces one embedding instead of two.
2. Pluggable idempotency backends (DynamoDB, S3 manifest, local JSON) via
   the `IdempotencyStore` Protocol — the orchestrator doesn't care which.
3. Atomic claim → embed → commit flow. A crash between claim and commit
   leaves a PENDING record that the next run reclaims (DynamoDB TTL or
   manifest staleness check), so failed chunks ARE retried.
4. Batched OpenAI calls with exponential backoff on rate-limit errors.
5. Env-var configuration (no hardcoded paths).
6. Structured `event=key=value` logging.

Cost on a 50k-chunk corpus, text-embedding-3-small @ $0.02/1M tokens,
~150 tokens/chunk:
    first run:           ~$0.15
    subsequent re-runs:  ~$0  (every chunk skipped via store.is_processed)
    annual (daily run):  ~$0.15  (assuming corpus doesn't grow)

That's the headline win. The deeper win is that the FAISS index never
contains duplicate vectors, so retrieval quality stays clean forever.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import numpy as np

from chunk import compute_chunk_id
from idempotency import IdempotencyStore, make_store_from_env
from sink import EmbeddingSink, LocalSink

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s level=%(levelname)s logger=%(name)s %(message)s",
)
log = logging.getLogger("embedding_pipeline")


# ---------------------------------------------------------------------------
# EmbedFn — the only OpenAI/HF coupling, kept behind a small typedef so
# tests can swap in a deterministic stub without monkey-patching.
# ---------------------------------------------------------------------------

EmbedFn = Callable[[list[str]], list[np.ndarray]]


def make_openai_embed_fn(
    model: str = "text-embedding-3-small",
    *,
    max_retries: int = 5,
    initial_backoff: float = 1.0,
) -> EmbedFn:
    """OpenAI batch-embedding function with exponential-backoff retries.

    Why exponential backoff: the OpenAI default rate limit is 3000 RPM for
    text-embedding-3-small. Above that we get 429s. Sleeping 1, 2, 4, 8, 16
    seconds gives a worst-case 31s before giving up — long enough to ride
    out a transient burst, short enough that genuine outages page humans.
    """
    import openai

    client = openai.OpenAI()

    def embed(texts: list[str]) -> list[np.ndarray]:
        backoff = initial_backoff
        for attempt in range(max_retries):
            try:
                resp = client.embeddings.create(model=model, input=texts)
                return [np.asarray(d.embedding, dtype="float32") for d in resp.data]
            except openai.RateLimitError:
                if attempt == max_retries - 1:
                    raise
                log.warning(
                    "event=rate_limited attempt=%d backoff_sec=%.1f",
                    attempt + 1, backoff,
                )
                time.sleep(backoff)
                backoff *= 2
        raise RuntimeError("unreachable")

    return embed


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineResult:
    """Summary of what one `embed_chunks` invocation accomplished."""
    total: int = 0
    embedded: int = 0
    skipped_processed: int = 0  # already in the manifest
    skipped_locked: int = 0     # claimed by another worker
    failed: int = 0

    def __str__(self) -> str:
        return (
            f"total={self.total} embedded={self.embedded} "
            f"skipped_processed={self.skipped_processed} "
            f"skipped_locked={self.skipped_locked} failed={self.failed}"
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def embed_chunks(
    chunks: Iterable[Mapping[str, Any]],
    *,
    store: IdempotencyStore,
    sink: EmbeddingSink,
    embed_fn: EmbedFn,
    batch_size: int = 64,
) -> PipelineResult:
    """Idempotently embed every chunk and persist via the sink.

    For each chunk:
        1. Compute a content-addressed chunk_id.
        2. If store.is_processed(id) → skip.
        3. Else store.claim(id):
              True  → embed, sink.write, store.mark_processed
              False → another worker has it; skip without embedding
        4. On any embedding/sink failure, store.release(id) so the chunk is
           retried on the next run.

    Batching happens at the embed layer — we collect up to `batch_size`
    successfully-claimed chunks before calling embed_fn once. This keeps
    OpenAI API calls efficient while preserving the per-chunk
    claim/commit guarantees.
    """
    result = PipelineResult.__class__  # for the typechecker; replaced below
    total = embedded = skipped_processed = skipped_locked = failed = 0

    pending: list[tuple[str, Mapping[str, Any]]] = []  # (chunk_id, chunk)

    def _flush() -> None:
        """Embed + commit everything in `pending`."""
        nonlocal embedded, failed
        if not pending:
            return
        texts = [c["text"] for _, c in pending]
        try:
            vectors = embed_fn(texts)
        except Exception:
            log.exception("event=embed_batch_failed size=%d", len(pending))
            for chunk_id, _ in pending:
                store.release(chunk_id)
                failed += 1
            pending.clear()
            return

        for (chunk_id, chunk), vector in zip(pending, vectors):
            try:
                uri = sink.write(chunk_id, vector, chunk)
                store.mark_processed(chunk_id, uri)
                embedded += 1
            except Exception:
                log.exception("event=sink_or_commit_failed chunk_id=%s", chunk_id[:12])
                store.release(chunk_id)
                failed += 1
        pending.clear()

    for chunk in chunks:
        total += 1
        chunk_id = compute_chunk_id(chunk)
        if store.is_processed(chunk_id):
            skipped_processed += 1
            continue
        if not store.claim(chunk_id):
            skipped_locked += 1
            continue
        pending.append((chunk_id, chunk))
        if len(pending) >= batch_size:
            _flush()

    _flush()

    log.info(
        "event=pipeline_complete total=%d embedded=%d "
        "skipped_processed=%d skipped_locked=%d failed=%d",
        total, embedded, skipped_processed, skipped_locked, failed,
    )
    return PipelineResult(
        total=total,
        embedded=embedded,
        skipped_processed=skipped_processed,
        skipped_locked=skipped_locked,
        failed=failed,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _load_chunks(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main() -> None:
    chunks_path = Path(os.environ["CHUNKS_PATH"])
    output_dir = Path(os.environ.get("OUTPUT_DIR", "./embeddings"))
    batch_size = int(os.environ.get("BATCH_SIZE", "64"))
    model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")

    chunks = _load_chunks(chunks_path)
    store = make_store_from_env()
    sink = LocalSink(output_dir)
    embed_fn = make_openai_embed_fn(model=model)

    log.info(
        "event=pipeline_start chunks=%d batch_size=%d model=%s store=%s",
        len(chunks), batch_size, model, type(store).__name__,
    )
    result = embed_chunks(
        chunks, store=store, sink=sink, embed_fn=embed_fn, batch_size=batch_size,
    )
    log.info("event=pipeline_summary %s", result)


if __name__ == "__main__":
    main()
