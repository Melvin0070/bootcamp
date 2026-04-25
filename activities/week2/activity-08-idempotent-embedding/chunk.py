"""Content-addressed chunk identity.

`chunk_id = sha256(canonical_text + canonical_metadata)` — deterministic,
collision-resistant, and safe to use as a primary key in DynamoDB or as a
filename in S3. Same content always yields the same id, so re-running the
pipeline over the same corpus never produces duplicate vectors.

Why SHA-256:
  - 256 bits gives a vanishingly small collision probability across any
    realistic corpus size (10⁹ chunks ≪ 2¹²⁸).
  - It's a CPU-bound hash — ~500 MB/s on a single core — fast enough that
    hashing is never the bottleneck of an embedding pipeline.
  - Producing a hex digest gives us a printable string that's safe in
    filenames, URLs, and DynamoDB keys without further encoding.

Why include metadata:
  - The same paragraph repeated in two documents is *intentionally* the same
    chunk (we want to dedup it). But two chunks that *happen* to share text
    but have different `chunk_index` or `section` should be distinct.
  - We hash a normalised JSON of the metadata so dict-key ordering doesn't
    perturb the id.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

# Fields included in the content hash. Adding a new field is a backward-
# compatibility break for the entire chunk_id namespace, so this list is
# deliberately small and additive only.
HASHED_METADATA_FIELDS = ("source", "section", "chunk_index")


def compute_chunk_id(chunk: Mapping[str, Any]) -> str:
    """Return a deterministic hex digest for a chunk dict.

    Required keys:
      - text: the chunk's body text
    Optional keys (used to disambiguate chunks with identical text):
      - source: e.g. an S3 URI or filename
      - section: e.g. "Methods" or "Discussion"
      - chunk_index: integer position within the source

    Any other keys (timestamps, processing flags, embedding URIs) are
    deliberately ignored — they don't affect content identity.
    """
    text = chunk.get("text")
    if not isinstance(text, str) or not text:
        raise ValueError("chunk['text'] must be a non-empty string")

    metadata = {k: chunk[k] for k in HASHED_METADATA_FIELDS if k in chunk}
    canonical = text + "␞" + json.dumps(metadata, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
