"""The Embedder interface.

A single, narrow contract every backend implements. Two provenance properties
(``model_id`` + ``dimensions``) are first-class because they *define the
vector space*: an index is only ever queried with vectors from the same
(model_id, dimensions) pair that built it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class Embedder(Protocol):
    """Turn text into unit-normalised float32 vectors.

    Implementations MUST return L2-normalised vectors so cosine similarity is
    a plain inner product and the same pgvector ``vector_cosine_ops`` index
    works regardless of backend.
    """

    @property
    def model_id(self) -> str:
        """Stable model identifier, recorded against every vector for provenance."""
        ...

    @property
    def dimensions(self) -> int:
        """Output vector dimensionality."""
        ...

    def encode(self, texts: list[str]) -> np.ndarray:
        """Embed a batch of texts.

        Returns an array of shape ``(len(texts), dimensions)``, dtype float32,
        each row L2-normalised.
        """
        ...

    def encode_one(self, text: str) -> np.ndarray:
        """Embed a single text, returning a 1-D ``(dimensions,)`` float32 vector."""
        ...
