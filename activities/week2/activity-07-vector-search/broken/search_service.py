"""
Vector search service — BROKEN.

Anti-patterns deliberately preserved so the diff against the fixed version is
crisp. This module is the "before" state for Activity 7.

Anti-patterns
-------------
1. Rebuilds the FAISS index on every /search request (the headline bug — >500ms p50).
2. Loads the SentenceTransformer model on every request as well, paying the
   download / weight-load cost in the request hot path.
3. Reads embeddings from disk inside the request handler instead of at startup.
4. No refresh mechanism — the only way to pick up new embeddings is a process
   restart, which drops in-flight traffic.
5. Hardcoded /data/... paths, so the same code can't run in dev / staging / prod.
6. print() logging — unstructured, unparseable in CloudWatch / Datadog, and
   doesn't include latency, vector counts, or error context.
7. No /healthz, /refresh, or /stats endpoints — operations team has no way to
   tell if the service is warm, stale, or wedged.

Rough latency profile on a 50k-vector / 384-dim corpus, MacBook M1:
    p50  ~ 540 ms   (npy load + FAISS add + model load + encode + search)
    p99  ~ 1.2 s    (cold cache, first request after deploy)
"""

from fastapi import FastAPI
import numpy as np
import faiss
import time
from sentence_transformers import SentenceTransformer

app = FastAPI()

EMBEDDINGS_PATH = "/data/fossilrag/embeddings.npy"


def build_index():
    """Loads embeddings from disk and builds a brand-new FAISS index."""
    print(f"Loading embeddings from {EMBEDDINGS_PATH}")
    embeddings = np.load(EMBEDDINGS_PATH).astype("float32")
    dim = embeddings.shape[1]
    print(f"Building FAISS IndexFlatIP with {len(embeddings)} vectors, dim={dim}")
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)
    return index


@app.get("/search")
def search(q: str, k: int = 5):
    start = time.time()
    # Anti-pattern: rebuild on EVERY request
    index = build_index()
    # Anti-pattern: also reloads the embedding model every time
    model = SentenceTransformer("all-MiniLM-L6-v2")
    query_vec = model.encode([q]).astype("float32")
    distances, indices = index.search(query_vec, k)
    elapsed_ms = (time.time() - start) * 1000
    print(f"Search took {elapsed_ms:.1f}ms")
    return {
        "query": q,
        "results": indices.tolist()[0],
        "distances": distances.tolist()[0],
        "latency_ms": elapsed_ms,
    }
