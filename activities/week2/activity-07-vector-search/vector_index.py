"""Thread-safe FAISS index wrapper.

Pulled out into its own module so it can be unit-tested without depending on
FastAPI / uvicorn / sentence-transformers.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

import faiss  # type: ignore[import-untyped]
import numpy as np

log = logging.getLogger("vector_search")


def build_faiss_index(embeddings: np.ndarray, kind: str) -> faiss.Index:
    """Construct a FAISS index of the requested kind.

    flat_ip — exact inner-product, best recall, ~O(N) per query. Use up to ~1M.
    hnsw    — graph-based ANN, ~O(log N), great default for 1-50M vectors.
    ivf_pq  — quantised inverted file, scales to 100M+ at the cost of recall.
    """
    dim = int(embeddings.shape[1])
    if kind == "flat_ip":
        idx: faiss.Index = faiss.IndexFlatIP(dim)
    elif kind == "hnsw":
        idx = faiss.IndexHNSWFlat(dim, 32)
    elif kind == "ivf_pq":
        nlist = max(1, int(np.sqrt(len(embeddings))))
        quantizer = faiss.IndexFlatIP(dim)
        idx = faiss.IndexIVFPQ(quantizer, dim, nlist, 8, 8)
        idx.train(embeddings)
    else:
        raise ValueError(f"Unknown INDEX_KIND={kind!r}")
    idx.add(embeddings)
    return idx


class VectorIndex:
    """Thread-safe holder for a FAISS index plus operational metadata.

    The lock is reentrant because `maybe_reload` calls `load`, which itself
    takes the lock to swap the index reference.
    """

    def __init__(self, path: Path, kind: str = "flat_ip") -> None:
        self.path = path
        self.kind = kind
        self._lock = threading.RLock()
        self._index: faiss.Index | None = None
        self._loaded_mtime: float | None = None
        self._size: int = 0
        self._dim: int | None = None
        self._loads: int = 0  # observability counter

    def load(self) -> None:
        """Load (or atomically swap) the index from disk."""
        if not self.path.exists():
            raise FileNotFoundError(f"Embeddings file not found: {self.path}")

        mtime = self.path.stat().st_mtime
        log.info("event=index_load_start path=%s mtime=%s kind=%s", self.path, mtime, self.kind)
        t0 = time.perf_counter()
        embeddings = np.load(self.path).astype("float32")
        dim = int(embeddings.shape[1])
        index = build_faiss_index(embeddings, self.kind)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        with self._lock:
            self._index = index
            self._size = int(len(embeddings))
            self._dim = dim
            self._loaded_mtime = mtime
            self._loads += 1

        log.info(
            "event=index_load_complete vectors=%d dim=%d kind=%s elapsed_ms=%.1f loads=%d",
            self._size, dim, self.kind, elapsed_ms, self._loads,
        )

    def maybe_reload(self) -> bool:
        """Reload only if the file's mtime has advanced. Returns True if reloaded."""
        if not self.path.exists():
            return False
        current_mtime = self.path.stat().st_mtime
        if self._loaded_mtime is not None and current_mtime <= self._loaded_mtime:
            return False
        self.load()
        return True

    def search(self, query_vec: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
        # Snapshot the index reference under the lock; FAISS search itself is
        # thread-safe for read-only workloads, so we release the lock before
        # blocking on the search.
        with self._lock:
            if self._index is None:
                raise RuntimeError("Index not loaded")
            idx = self._index
        return idx.search(query_vec, k)

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "vectors": self._size,
                "dim": self._dim,
                "kind": self.kind,
                "loaded_mtime": self._loaded_mtime,
                "loads": self._loads,
            }
