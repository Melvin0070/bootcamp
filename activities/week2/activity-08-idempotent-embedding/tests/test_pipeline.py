"""
Test suite for Activity 8 — idempotent embedding pipeline.

Two layers, same shape as Activity 7:

1. Source-file analysis — anti-patterns are preserved in `broken/`, fixes
   are present in the production modules. No third-party imports needed.

2. Behaviour tests — the orchestrator + LocalManifestStore + LocalSink end
   to end with a deterministic stub `embed_fn`. Verifies:
       a) Re-running skips already-processed chunks (no duplicate API calls)
       b) New chunks are embedded exactly once
       c) Crash mid-batch leaves PENDING claims; next run reclaims them
       d) Concurrent claims (in-process threads) — exactly one wins
       e) Content-addressed ids dedupe identical chunks across documents
"""

from __future__ import annotations

import ast
import json
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BROKEN = (ROOT / "broken" / "embedding_pipeline.py").read_text(encoding="utf-8")
PIPELINE = (ROOT / "embedding_pipeline.py").read_text(encoding="utf-8")
IDEMPOTENCY = (ROOT / "idempotency.py").read_text(encoding="utf-8")
CHUNK = (ROOT / "chunk.py").read_text(encoding="utf-8")
SINK = (ROOT / "sink.py").read_text(encoding="utf-8")
ALL_FIXED = "\n".join([PIPELINE, IDEMPOTENCY, CHUNK, SINK])


# ===========================================================================
# Layer 1 — broken file demonstrates every anti-pattern
# ===========================================================================


class TestBrokenAntiPatterns:
    def test_uses_sequential_int_id(self):
        # The smoking gun — `f"{i}.npy"` filename.
        assert re.search(r'f["\'].*\{i\}.*npy', BROKEN), (
            "broken file should use a sequential int as the embedding id"
        )

    def test_no_idempotency_check(self):
        assert "is_processed" not in BROKEN
        assert "manifest" not in BROKEN.lower()
        assert "DynamoDB" not in BROKEN
        assert "ConditionExpression" not in BROKEN

    def test_no_content_hashing(self):
        assert "sha256" not in BROKEN
        assert "compute_chunk_id" not in BROKEN
        assert "hashlib" not in BROKEN

    def test_no_batching(self):
        # Anti-pattern: one API call per chunk.
        match = re.search(
            r"def\s+embed_chunks\s*\([^)]*\)\s*:.*?(?=\ndef|\Z)",
            BROKEN,
            flags=re.DOTALL,
        )
        body = match.group(0)
        assert "for" in body
        assert "call_openai_api" in body
        # Confirm the call sits inside the loop, one per iteration.
        loop_to_call = body.split("for", 1)[1]
        assert "call_openai_api(" in loop_to_call

    def test_uses_print_logging(self):
        assert "print(" in BROKEN

    def test_hardcoded_paths(self):
        assert "/data/fossilrag" in BROKEN
        assert "os.environ" not in BROKEN

    def test_no_retry_logic(self):
        assert "RateLimitError" not in BROKEN
        assert "backoff" not in BROKEN.lower()


# ===========================================================================
# Layer 1 — fixed code uses every required pattern
# ===========================================================================


class TestFixedPatterns:
    def test_content_addressed_ids(self):
        assert "sha256" in CHUNK
        assert "compute_chunk_id" in CHUNK
        # The hash includes both text and metadata.
        assert "HASHED_METADATA_FIELDS" in CHUNK
        assert "json.dumps" in CHUNK

    def test_idempotency_protocol_defined(self):
        tree = ast.parse(IDEMPOTENCY)
        protocols = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "IdempotencyStore"
        ]
        assert protocols, "IdempotencyStore Protocol must be defined"
        method_names = {n.name for n in protocols[0].body if isinstance(n, ast.FunctionDef)}
        assert {"is_processed", "claim", "mark_processed", "release"} <= method_names

    def test_three_backends_implemented(self):
        for cls in ("LocalManifestStore", "DynamoDBStore", "S3ManifestStore"):
            assert f"class {cls}" in IDEMPOTENCY, f"{cls} must be implemented"

    def test_dynamodb_uses_conditional_writes(self):
        # The whole point — atomic claims via ConditionExpression.
        assert "ConditionExpression" in IDEMPOTENCY
        assert "attribute_not_exists" in IDEMPOTENCY
        assert "ConditionalCheckFailedException" in IDEMPOTENCY

    def test_dynamodb_uses_ttl_for_pending(self):
        assert '"ttl"' in IDEMPOTENCY or "'ttl'" in IDEMPOTENCY

    def test_pipeline_uses_claim_embed_commit_flow(self):
        tree = ast.parse(PIPELINE)
        embed_chunks_fn = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "embed_chunks"),
            None,
        )
        assert embed_chunks_fn is not None
        body = ast.unparse(embed_chunks_fn)
        assert "is_processed" in body
        assert "claim" in body
        assert "mark_processed" in body
        assert "release" in body  # for failure path

    def test_pipeline_uses_batching(self):
        assert "batch_size" in PIPELINE
        assert "_flush" in PIPELINE or "flush" in PIPELINE

    def test_uses_env_vars(self):
        assert "os.environ" in PIPELINE
        assert "IDEMPOTENCY_BACKEND" in IDEMPOTENCY
        assert "CHUNKS_PATH" in PIPELINE

    def test_structured_logging(self):
        assert "logging.getLogger" in PIPELINE
        assert "event=" in PIPELINE
        assert "print(" not in PIPELINE
        assert "print(" not in IDEMPOTENCY

    def test_retry_logic_for_rate_limits(self):
        assert "RateLimitError" in PIPELINE
        assert "backoff" in PIPELINE.lower()

    def test_atomic_sink_writes(self):
        # LocalSink should write to a tmp file and replace, never partial.
        assert "tmp" in SINK
        assert ".replace(" in SINK or "os.replace" in SINK

    def test_modules_parse_cleanly(self):
        ast.parse(BROKEN)
        ast.parse(PIPELINE)
        ast.parse(IDEMPOTENCY)
        ast.parse(CHUNK)
        ast.parse(SINK)


# ===========================================================================
# Layer 2 — behavioural tests
# ===========================================================================


class StubEmbedder:
    """Deterministic embed_fn — counts calls and returns synthetic vectors."""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.calls: list[list[str]] = []

    def __call__(self, texts: list[str]) -> list[np.ndarray]:
        self.calls.append(list(texts))
        return [
            np.full(self.dim, hash(t) % 100, dtype="float32") for t in texts
        ]


@pytest.fixture
def store(tmp_path):
    from idempotency import LocalManifestStore
    return LocalManifestStore(tmp_path / "manifest.json")


@pytest.fixture
def sink(tmp_path):
    from sink import LocalSink
    return LocalSink(tmp_path / "embeddings")


@pytest.fixture
def chunks():
    return [
        {"text": "trex jaw fragment", "source": "doc1.pdf", "chunk_index": 0},
        {"text": "stegosaurus plate", "source": "doc1.pdf", "chunk_index": 1},
        {"text": "ammonite spiral", "source": "doc2.pdf", "chunk_index": 0},
    ]


def test_first_run_embeds_every_chunk(store, sink, chunks):
    from embedding_pipeline import embed_chunks
    embedder = StubEmbedder()

    result = embed_chunks(chunks, store=store, sink=sink, embed_fn=embedder, batch_size=64)

    assert result.embedded == 3
    assert result.skipped_processed == 0
    assert sum(len(b) for b in embedder.calls) == 3
    assert store.stats() == {"processed": 3, "pending": 0}


def test_second_run_is_a_noop(store, sink, chunks):
    """Re-running over the same chunks must not call the embedder at all."""
    from embedding_pipeline import embed_chunks
    embedder = StubEmbedder()
    embed_chunks(chunks, store=store, sink=sink, embed_fn=embedder, batch_size=64)

    embedder2 = StubEmbedder()
    result = embed_chunks(chunks, store=store, sink=sink, embed_fn=embedder2, batch_size=64)

    assert result.embedded == 0
    assert result.skipped_processed == 3
    assert embedder2.calls == [], "embedder must not be called on a no-op re-run"


def test_partial_failure_leaves_failed_chunks_for_next_run(tmp_path, store, sink, chunks):
    """A crash mid-batch leaves PENDING claims; next run reclaims them."""
    from embedding_pipeline import embed_chunks

    # First run: embedder raises on every call.
    def boom(_):
        raise RuntimeError("api blew up")

    result = embed_chunks(chunks, store=store, sink=sink, embed_fn=boom, batch_size=64)
    assert result.failed == 3
    assert result.embedded == 0
    # release() should clear the PENDING records so the next run picks them up.
    assert store.stats() == {"processed": 0, "pending": 0}

    # Second run with a working embedder — should embed everything.
    embedder = StubEmbedder()
    result2 = embed_chunks(chunks, store=store, sink=sink, embed_fn=embedder, batch_size=64)
    assert result2.embedded == 3
    assert sum(len(b) for b in embedder.calls) == 3


def test_dedup_across_documents(store, sink):
    """Same text + same metadata fields → same chunk_id → embedded once."""
    from embedding_pipeline import embed_chunks

    chunks = [
        {"text": "trex jaw fragment", "source": "shared", "chunk_index": 0},
        {"text": "trex jaw fragment", "source": "shared", "chunk_index": 0},
    ]
    embedder = StubEmbedder()
    result = embed_chunks(chunks, store=store, sink=sink, embed_fn=embedder, batch_size=64)
    # The second occurrence is identical to the first, so one of:
    #   - it was already PROCESSED by the time the second one was seen, OR
    #   - it was claimed within the same batch and the duplicate fails the claim
    # Either way: only one call to the embedder, only one PROCESSED record.
    assert result.embedded == 1
    assert sum(len(b) for b in embedder.calls) == 1
    assert store.stats()["processed"] == 1


def test_distinct_metadata_produces_distinct_ids():
    """Same text under different chunk_index must NOT dedupe."""
    from chunk import compute_chunk_id

    a = compute_chunk_id({"text": "shared text", "source": "x", "chunk_index": 0})
    b = compute_chunk_id({"text": "shared text", "source": "x", "chunk_index": 1})
    assert a != b


def test_chunk_id_is_deterministic():
    from chunk import compute_chunk_id
    chunk = {"text": "trex jaw", "source": "doc.pdf", "chunk_index": 3}
    assert compute_chunk_id(chunk) == compute_chunk_id(chunk)


def test_chunk_id_ignores_unhashed_fields():
    from chunk import compute_chunk_id
    a = {"text": "trex jaw", "source": "x", "chunk_index": 0}
    b = {**a, "embedding_uri": "s3://...", "processed_at": 12345}
    assert compute_chunk_id(a) == compute_chunk_id(b)


def test_concurrent_claims_only_one_wins(tmp_path):
    """Two threads racing for the same chunk_id — at most one .claim() returns True."""
    from idempotency import LocalManifestStore

    store = LocalManifestStore(tmp_path / "m.json")
    chunk_id = "a" * 64
    results: list[bool] = []
    barrier = threading.Barrier(8)

    def race():
        barrier.wait()
        results.append(store.claim(chunk_id))

    threads = [threading.Thread(target=race) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for r in results if r) == 1, f"exactly one winner, got {results}"


def test_release_clears_pending_only(tmp_path):
    """release() must not clobber a PROCESSED record."""
    from idempotency import LocalManifestStore
    store = LocalManifestStore(tmp_path / "m.json")
    cid = "b" * 64
    assert store.claim(cid)
    store.mark_processed(cid, "/tmp/v.npy")
    store.release(cid)  # should be a no-op
    assert store.is_processed(cid)


def test_factory_picks_local_by_default(tmp_path, monkeypatch):
    from idempotency import make_store_from_env, LocalManifestStore
    monkeypatch.delenv("IDEMPOTENCY_BACKEND", raising=False)
    monkeypatch.setenv("MANIFEST_PATH", str(tmp_path / "m.json"))
    store = make_store_from_env()
    assert isinstance(store, LocalManifestStore)


def test_factory_picks_dynamodb_when_requested(monkeypatch):
    from idempotency import make_store_from_env, DynamoDBStore
    monkeypatch.setenv("IDEMPOTENCY_BACKEND", "dynamodb")
    monkeypatch.setenv("IDEMPOTENCY_TABLE", "ChunkRegistry")

    # Stub boto3 so this test doesn't require AWS creds.
    fake_boto3 = type(sys)("boto3")
    fake_boto3.client = lambda *a, **kw: object()
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    store = make_store_from_env()
    assert isinstance(store, DynamoDBStore)


def test_pending_ttl_allows_reclaim(tmp_path, monkeypatch):
    """A PENDING record older than TTL is reclaimable."""
    from idempotency import LocalManifestStore

    store = LocalManifestStore(tmp_path / "m.json")
    monkeypatch.setattr(store, "PENDING_TTL_SEC", 0)  # everything is stale
    cid = "c" * 64
    assert store.claim(cid) is True
    # Without TTL relief, the second claim would fail. With TTL=0, it succeeds.
    time.sleep(0.01)
    assert store.claim(cid) is True
