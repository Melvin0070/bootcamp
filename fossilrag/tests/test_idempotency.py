"""Idempotency ledger + idempotent embed pipeline (Self-Healing Idempotency)."""

from __future__ import annotations

import pytest

from fossilrag.config import Settings
from fossilrag.embedding.mock import MockEmbedder
from fossilrag.embedding.pipeline import embed_chunks
from fossilrag.idempotency import (
    DynamoDBStore,
    LocalManifestStore,
    NullStore,
    make_idempotency_store,
)
from fossilrag.models import Chunk, compute_chunk_id, geological_age_for


def _chunks(texts):
    return [
        Chunk(
            chunk_id=compute_chunk_id("doc", i, t),
            doc_id="doc",
            ordinal=i,
            content=t,
            char_count=len(t),
            layer_version=1,
            geological_age=geological_age_for(1),
        )
        for i, t in enumerate(texts)
    ]


class _FakeVectorStore:
    """Minimal async VectorStore stand-in recording upsert batch sizes."""

    def __init__(self) -> None:
        self.upserts: list[int] = []

    async def upsert_chunks(self, chunks, vectors):  # noqa: ANN001
        assert len(chunks) == len(vectors)
        self.upserts.append(len(chunks))
        return len(chunks)


# --- ledger backends ------------------------------------------------------


def test_null_store_never_processes():
    s = NullStore()
    assert s.claim("k") is True
    s.mark_processed("k", "ref")
    assert s.is_processed("k") is False  # null is a no-op by design


def test_local_manifest_store_lifecycle(tmp_path):
    s = LocalManifestStore(tmp_path / "idem.json")
    k = "m:8:abc"
    assert not s.is_processed(k)
    assert s.claim(k) is True
    assert s.claim(k) is False  # already PENDING
    s.mark_processed(k, "vector:abc")
    assert s.is_processed(k)
    assert s.claim(k) is False  # already PROCESSED
    assert s.stats() == {"processed": 1, "pending": 0}


def test_local_manifest_release_makes_reclaimable(tmp_path):
    s = LocalManifestStore(tmp_path / "idem.json")
    assert s.claim("k") is True
    s.release("k")
    assert s.claim("k") is True  # reclaimable after release


def test_make_idempotency_store(tmp_path):
    assert isinstance(make_idempotency_store(Settings(idempotency_backend="null")), NullStore)
    s = make_idempotency_store(
        Settings(idempotency_backend="local", idempotency_manifest_path=str(tmp_path / "i.json"))
    )
    assert isinstance(s, LocalManifestStore)
    with pytest.raises(ValueError):  # dynamodb needs a table
        make_idempotency_store(Settings(idempotency_backend="dynamodb"))


# --- idempotent embed pipeline --------------------------------------------


async def test_embed_chunks_skips_already_processed(tmp_path):
    chunks = _chunks(["alpha", "beta", "gamma"])
    store = _FakeVectorStore()
    emb = MockEmbedder(dimensions=16)
    idem = LocalManifestStore(tmp_path / "idem.json")

    r1 = await embed_chunks(chunks, store=store, embedder=emb, idempotency=idem)
    assert (r1.embedded, r1.skipped_processed, r1.failed) == (3, 0, 0)

    r2 = await embed_chunks(chunks, store=store, embedder=emb, idempotency=idem)
    assert (r2.embedded, r2.skipped_processed) == (0, 3)  # all skipped on re-run
    assert store.upserts == [3]  # only the first run actually upserted


async def test_embed_chunks_releases_claims_on_failure(tmp_path):
    class _BoomEmbedder:
        model_id = "boom"
        dimensions = 16

        def encode(self, texts):  # noqa: ANN001
            raise RuntimeError("embed failed")

    chunks = _chunks(["alpha", "beta"])
    store = _FakeVectorStore()
    idem = LocalManifestStore(tmp_path / "idem.json")

    r = await embed_chunks(chunks, store=store, embedder=_BoomEmbedder(), idempotency=idem)
    assert (r.embedded, r.failed) == (0, 2)
    assert store.upserts == []
    # Claims were released → a subsequent run with a working embedder succeeds.
    r2 = await embed_chunks(
        chunks, store=store, embedder=MockEmbedder(dimensions=16), idempotency=idem
    )
    assert r2.embedded == 2


async def test_embed_chunks_null_store_always_embeds(tmp_path):
    chunks = _chunks(["x", "y"])
    store = _FakeVectorStore()
    emb = MockEmbedder(dimensions=16)
    r1 = await embed_chunks(chunks, store=store, embedder=emb)  # default NullStore
    r2 = await embed_chunks(chunks, store=store, embedder=emb)
    assert r1.embedded == 2 and r2.embedded == 2  # no skipping without a real ledger
    assert store.upserts == [2, 2]


# --- DynamoDB ledger (moto) ----------------------------------------------


def test_dynamodb_store_atomic_claim_and_commit():
    pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    from moto import mock_aws

    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="idem",
            KeySchema=[{"AttributeName": "key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        s = DynamoDBStore("idem", client=ddb)
        k = "m:1024:abc"
        assert not s.is_processed(k)
        assert s.claim(k) is True
        assert s.claim(k) is False  # PENDING, not expired → conditional write fails
        s.mark_processed(k, "vector:abc")
        assert s.is_processed(k)
        assert s.claim(k) is False  # PROCESSED → not reclaimable
        assert s.stats()["processed"] == 1


def _ddb_table(boto3):  # noqa: ANN001
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    ddb.create_table(
        TableName="idem",
        KeySchema=[{"AttributeName": "key", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "key", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    return ddb


def test_dynamodb_store_release_reclaims():
    pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    from moto import mock_aws

    with mock_aws():
        s = DynamoDBStore("idem", client=_ddb_table(boto3))
        assert s.claim("k") is True
        s.release("k")
        assert s.claim("k") is True  # released → reclaimable


def test_dynamodb_store_reclaims_expired_pending():
    """The self-healing branch: a dead worker's expired PENDING claim is reclaimable."""
    pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    from moto import mock_aws

    with mock_aws():
        ddb = _ddb_table(boto3)
        # Seed a PENDING claim whose TTL is far in the past (a crashed worker).
        ddb.put_item(
            TableName="idem",
            Item={
                "key": {"S": "k"},
                "status": {"S": "PENDING"},
                "claimed_at": {"N": "0"},
                "ttl": {"N": "1"},  # 1970 << now → expired
            },
        )
        s = DynamoDBStore("idem", client=ddb)
        assert s.claim("k") is True  # expired pending → atomically reclaimed
