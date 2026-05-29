"""OpenAI-compatible embedder (OpenAI / Google Gemini / Ollama / …).

The embedding twin of :mod:`fossilrag.llm.openai_compat`: one ``base_url``
selects the vendor (e.g. Gemini's free OpenAI-compat endpoint with
``text-embedding-004``, or OpenAI ``text-embedding-3-small``). Vectors are
L2-normalised here so the Embedder contract holds regardless of whether the
vendor returns normalised vectors. ``embed_dim`` must match the model's native
dimensionality (e.g. 768 for Gemini text-embedding-004, 1536 for OpenAI
3-small); a mismatch raises rather than silently corrupting the index.

The ``openai`` SDK is the optional ``openai`` extra, imported lazily; the client
is **injectable** for $0 unit tests.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from fossilrag.logging import get_logger

log = get_logger("embedding.openai")

DEFAULT_MODEL = "text-embedding-3-small"


class OpenAICompatEmbedder:
    def __init__(
        self,
        model_id: str = DEFAULT_MODEL,
        *,
        dimensions: int,
        base_url: str | None = None,
        api_key: str | None = None,
        client: Any = None,
    ) -> None:
        self._model_id = model_id
        self._dim = int(dimensions)
        self._base_url = base_url
        self._api_key = api_key
        self._client = client

    def _ensure_client(self):  # noqa: ANN202
        if self._client is None:
            from openai import OpenAI  # lazy, optional ('openai' extra)

            self._client = OpenAI(base_url=self._base_url, api_key=self._api_key)
        return self._client

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dim

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self._dim), dtype=np.float32)
        resp = self._ensure_client().embeddings.create(model=self._model_id, input=texts)
        vecs = np.asarray([d.embedding for d in resp.data], dtype=np.float32)
        if vecs.shape[1] != self._dim:
            raise ValueError(
                f"{self._model_id} returned dim {vecs.shape[1]}, expected {self._dim} "
                f"(set FOSSILRAG_EMBED_DIM to the model's native dimensionality)"
            )
        # L2-normalise so cosine similarity is a plain inner product.
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (vecs / norms).astype(np.float32)

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]
