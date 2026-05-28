"""Deterministic, dependency-free embedder for dev, CI, and the walking skeleton.

Why a mock embedder at all? Because the *real* embedders are exactly the parts
that cost money or can't run at $0 (Bedrock Titan v2 is not free; local
sentence-transformers pulls ~2GB of torch). The mock lets the entire
ingest → chunk → embed → store → retrieve spine run — and be tested
end-to-end in CI — with no keys, no network, and no heavyweight wheels.

Determinism: each text is hashed to a seed, so the same text always maps to
the same vector across processes and machines. Vectors are L2-normalised, so
cosine similarity behaves and the pgvector ``vector_cosine_ops`` index returns
sensible neighbours (identical text => similarity 1.0). It is NOT semantic —
two paraphrases are unrelated — which is the whole point: swap in a real
embedder (PR3) and retrieval quality appears without any other code change.
"""

from __future__ import annotations

import hashlib

import numpy as np


class MockEmbedder:
    """Hash-seeded, L2-normalised pseudo-embeddings. Deterministic by construction."""

    def __init__(self, model_id: str = "mock-deterministic-v1", dimensions: int = 384) -> None:
        if dimensions < 2:
            raise ValueError("dimensions must be >= 2")
        self._model_id = model_id
        self._dim = int(dimensions)

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dim

    def _seed(self, text: str) -> int:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # First 8 bytes → unsigned 64-bit seed, masked to numpy's accepted range.
        return int.from_bytes(digest[:8], "big") % (2**32)

    def encode_one(self, text: str) -> np.ndarray:
        rng = np.random.default_rng(self._seed(text))
        vec = rng.standard_normal(self._dim).astype(np.float32)
        norm = np.linalg.norm(vec)
        if norm == 0.0:  # astronomically unlikely, but keep it total
            vec[0] = 1.0
            norm = 1.0
        return vec / norm

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self._dim), dtype=np.float32)
        return np.vstack([self.encode_one(t) for t in texts])
