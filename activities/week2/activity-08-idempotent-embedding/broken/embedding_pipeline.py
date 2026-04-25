"""
Embedding generation pipeline — BROKEN.

Anti-patterns deliberately preserved as the "before" state for Activity 8.

Anti-patterns
-------------
1. No idempotency — every run re-embeds every chunk, even ones that were
   successfully embedded last time. Cost grows linearly with re-runs.
2. No content addressing — chunk_id is a sequential integer, so two runs
   over the same document produce different ids and append duplicate vectors
   to the index instead of dedup'ing.
3. No deduplication across documents — if two PDFs share a paragraph it gets
   embedded twice, polluting search results with the same chunk under two ids.
4. No partial-failure recovery — a process crash in the middle of a batch
   leaves no record of what got done; the next run re-embeds everything.
5. Synchronous, one-call-per-chunk OpenAI API hits → rate-limit blow-up at
   ~3000 chunks (OpenAI default is 3000 RPM for text-embedding-3-small).
6. Hardcoded paths and `print()` logging (same anti-patterns as Activity 7's
   broken version — this is the "before" baseline for the whole pipeline).

Cost model on a 50k-chunk corpus, text-embedding-3-small @ $0.02/1M tokens,
~150 tokens/chunk:
    one run:           ~$0.15  (50k × 150 / 1e6 × $0.02)
    five re-runs:      ~$0.75  (5×, 100% wasted because nothing changed)
    annual (daily run): ~$55   (~365×, 99.7% wasted)

The monthly bill is annoying. The duplicate vectors are the real bug — they
silently degrade retrieval quality forever.
"""

import json
import os
from pathlib import Path

import numpy as np
import openai

# Anti-pattern: hardcoded paths
INPUT_PATH = "/data/fossilrag/chunks.jsonl"
OUTPUT_DIR = "/data/fossilrag/embeddings/"


def call_openai_api(text: str) -> list[float]:
    # Anti-pattern: synchronous, no batching, no retry
    resp = openai.embeddings.create(model="text-embedding-3-small", input=text)
    return resp.data[0].embedding


def embed_chunks(chunks):
    """Embed every chunk and write to disk by sequential id."""
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    for i, chunk in enumerate(chunks):
        # Anti-pattern: sequential int id — non-deterministic, breaks dedup
        embedding = call_openai_api(chunk["text"])
        out_path = Path(OUTPUT_DIR) / f"{i}.npy"
        np.save(out_path, np.asarray(embedding, dtype="float32"))
        print(f"Embedded chunk {i} → {out_path}")  # anti-pattern: print logging


def main():
    with open(INPUT_PATH) as f:
        chunks = [json.loads(line) for line in f]
    print(f"Embedding {len(chunks)} chunks")
    embed_chunks(chunks)
    print("Done")


if __name__ == "__main__":
    main()
