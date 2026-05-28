"""Text cleaning & semantic chunking → gold-layer fossil fragments.

PR0 ships a naive paragraph splitter so the spine produces chunks. PR2 deepens
this into real cleaning (control chars, whitespace normalisation) and
token-aware semantic chunking with overlap, plus versioned fossil layers.
"""

from fossilrag.chunking.chunker import chunk_document

__all__ = ["chunk_document"]
