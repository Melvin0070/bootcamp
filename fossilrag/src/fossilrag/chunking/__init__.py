"""Text cleaning & semantic chunking → gold-layer fossil fragments.

Cleans raw text and splits it into token-budgeted, overlapping semantic chunks
(``chunker``/``text``), then persists them as versioned gold-layer artifacts
(``gold``: JSONL default, Parquet optional).
"""

from fossilrag.chunking.chunker import chunk_document, chunk_text
from fossilrag.chunking.gold import write_gold
from fossilrag.chunking.text import clean_text, estimate_tokens, split_sentences

__all__ = [
    "chunk_document",
    "chunk_text",
    "clean_text",
    "split_sentences",
    "estimate_tokens",
    "write_gold",
]
