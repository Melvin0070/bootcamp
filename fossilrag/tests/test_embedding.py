"""Embedder tests — real backends exercised via injected fakes ($0, no torch/boto3)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from fossilrag.config import Settings
from fossilrag.embedding import make_embedder
from fossilrag.embedding.bedrock import DEFAULT_MODEL as TITAN
from fossilrag.embedding.bedrock import BedrockEmbedder
from fossilrag.embedding.local import DEFAULT_MODEL as MINILM
from fossilrag.embedding.local import LocalEmbedder

# --- LocalEmbedder (injected sentence-transformers stand-in) --------------


class _FakeST:
    def __init__(self, dim: int = 8) -> None:
        self._dim = dim

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    def encode(self, texts, normalize_embeddings=True):  # noqa: ANN001
        arr = np.array(
            [[float(len(t)), 1.0, 2.0] + [0.0] * (self._dim - 3) for t in texts], np.float32
        )
        if normalize_embeddings:
            arr = arr / np.linalg.norm(arr, axis=1, keepdims=True)
        return arr


def test_local_embedder_wraps_model():
    e = LocalEmbedder(model_name="fake-mini", model=_FakeST(dim=8))
    assert e.model_id == "fake-mini"
    assert e.dimensions == 8
    v = e.encode_one("hello")
    assert v.shape == (8,) and v.dtype == np.float32
    assert np.isclose(np.linalg.norm(v), 1.0, atol=1e-5)  # normalised
    assert e.encode(["a", "bb"]).shape == (2, 8)
    assert e.encode([]).shape == (0, 8)


# --- BedrockEmbedder (injected bedrock-runtime stand-in) ------------------


class _FakeBody:
    def __init__(self, payload: dict) -> None:
        self._p = payload

    def read(self) -> bytes:
        return json.dumps(self._p).encode()


class _FakeBedrock:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def invoke_model(self, **kwargs):  # noqa: ANN003
        body = json.loads(kwargs["body"])
        self.calls.append({"modelId": kwargs["modelId"], **body})
        d = body["dimensions"]
        vec = [0.0] * d
        vec[0] = 1.0
        return {"body": _FakeBody({"embedding": vec, "inputTextTokenCount": 3})}


def test_bedrock_embedder_request_and_parse():
    fake = _FakeBedrock()
    e = BedrockEmbedder(dimensions=512, client=fake)
    assert e.model_id == TITAN and e.dimensions == 512
    v = e.encode_one("trex")
    assert v.shape == (512,)
    assert fake.calls[0]["inputText"] == "trex"
    assert fake.calls[0]["dimensions"] == 512
    assert fake.calls[0]["normalize"] is True
    assert e.encode(["a", "b"]).shape == (2, 512)
    assert len(fake.calls) == 3  # one invoke_model per text + the encode_one above
    assert e.encode([]).shape == (0, 512)


def test_bedrock_embedder_rejects_unsupported_dim():
    with pytest.raises(ValueError):
        BedrockEmbedder(dimensions=384)  # Titan v2 only does 256/512/1024


def test_bedrock_embedder_detects_dim_mismatch():
    class _Bad:
        def invoke_model(self, **k):  # noqa: ANN003
            return {"body": _FakeBody({"embedding": [1.0, 2.0, 3.0]})}

    e = BedrockEmbedder(dimensions=1024, client=_Bad())
    with pytest.raises(ValueError):
        e.encode_one("x")


# --- factory dispatch -----------------------------------------------------


def test_make_embedder_local_falls_back_to_default_model():
    e = make_embedder(Settings(embed_provider="local", embed_model="mock-deterministic-v1"))
    assert isinstance(e, LocalEmbedder)
    assert e.model_id == MINILM  # mock sentinel → default local model


def test_make_embedder_bedrock_dispatch():
    e = make_embedder(
        Settings(embed_provider="bedrock", embed_model="mock-deterministic-v1", embed_dim=1024)
    )
    assert isinstance(e, BedrockEmbedder)
    assert e.model_id == TITAN and e.dimensions == 1024
