"""Local CPU embedder — sentence-transformers (the dev/offline fallback).

Default model ``all-MiniLM-L6-v2`` (384-dim, ~22MB). sentence-transformers
pulls torch (~2GB) and will NOT fit a Lambda zip — which is exactly why Bedrock
Titan v2 is the *cloud* default and this is a dev/compose fallback (run it in a
container, never a bare Lambda). The dependency is the optional
``local-embeddings`` extra and is imported lazily.

The model is **injectable** (``model=``) so the wrapper — normalisation, dim,
provenance — is unit-tested with a tiny fake, with no torch download.
"""

from __future__ import annotations

import numpy as np

from fossilrag.logging import get_logger

log = get_logger("embedding.local")

DEFAULT_MODEL = "all-MiniLM-L6-v2"


class LocalEmbedder:
    """sentence-transformers embedder producing L2-normalised float32 vectors."""

    def __init__(self, model_name: str = DEFAULT_MODEL, *, model: object | None = None) -> None:
        self._model_name = model_name or DEFAULT_MODEL
        self._model = model  # injected fake in tests; lazily loaded otherwise

    def _ensure_model(self):  # noqa: ANN202
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # lazy, optional

            log.info("event=local_model_load model=%s", self._model_name)
            self._model = SentenceTransformer(self._model_name)
        return self._model

    @property
    def model_id(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return int(self._ensure_model().get_sentence_embedding_dimension())

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dimensions), dtype=np.float32)
        # normalize_embeddings=True → unit vectors, so cosine == inner product
        # and the same pgvector cosine index works across backends.
        vecs = self._ensure_model().encode(texts, normalize_embeddings=True)
        return np.asarray(vecs, dtype=np.float32)

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]
