"""Semantic, token-aware chunking → gold-layer fossil fragments.

Strategy (deepens PR0's naive paragraph split):

1. **Clean** the raw text (control chars, whitespace, unicode) — see ``text``.
2. **Segment** into sentences (paragraph-aware: a heading or punctuation-less
   line is its own segment).
3. **Pack** segments greedily up to ``max_tokens`` with a **sentence-level
   overlap** of ~``overlap_tokens`` between consecutive chunks, so a fact that
   straddles a boundary is retrievable from either side — the standard RAG
   chunking shape, done dependency-free.

Each chunk is a content-addressed :class:`Chunk` tagged with its fossil-layer
version + geological age, so re-running over the same cleaned text yields
identical ids (idempotent), and a new document version is a new addressable
layer (Time-Travel, PR5).
"""

from __future__ import annotations

from fossilrag.chunking.text import clean_text, estimate_tokens, paragraphs, split_sentences
from fossilrag.logging import get_logger
from fossilrag.models import Chunk, RawDocument, compute_chunk_id, geological_age_for

log = get_logger("chunking")


def _overlap_tail(segments: list[str], overlap_tokens: int) -> tuple[list[str], int]:
    """Trailing segments whose token sum is ~overlap_tokens (seeds the next chunk)."""
    if overlap_tokens <= 0:
        return [], 0
    tail: list[str] = []
    tok = 0
    for seg in reversed(segments):
        t = estimate_tokens(seg)
        if tail and tok + t > overlap_tokens:
            break
        tail.insert(0, seg)
        tok += t
    return tail, tok


def chunk_text(text: str, *, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Pack cleaned text into ~``max_tokens`` chunks with sentence overlap.

    A single segment longer than ``max_tokens`` becomes its own (oversized)
    chunk rather than being hard-split — rare for real prose, and hard-splitting
    mid-sentence hurts retrieval more than an occasional large chunk.
    """
    # Guarantee forward progress even if misconfigured (overlap >= max).
    overlap_tokens = min(overlap_tokens, max(0, max_tokens - 1))

    segments: list[str] = []
    for para in paragraphs(text):
        segments.extend(split_sentences(para) or [para])
    if not segments:
        return []

    chunks: list[str] = []
    cur: list[str] = []
    cur_tok = 0
    for seg in segments:
        st = estimate_tokens(seg)
        if cur and cur_tok + st > max_tokens:
            chunks.append(" ".join(cur))
            cur, cur_tok = _overlap_tail(cur, overlap_tokens)
        cur.append(seg)
        cur_tok += st
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def chunk_document(
    doc: RawDocument,
    *,
    layer_version: int = 1,
    max_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> list[Chunk]:
    """Clean + semantically chunk a document into ordered :class:`Chunk` fossils."""
    if max_tokens is None or overlap_tokens is None:
        from fossilrag.config import get_settings

        s = get_settings()
        max_tokens = s.chunk_max_tokens if max_tokens is None else max_tokens
        overlap_tokens = s.chunk_overlap_tokens if overlap_tokens is None else overlap_tokens

    cleaned = clean_text(doc.text)
    pieces = chunk_text(cleaned, max_tokens=max_tokens, overlap_tokens=overlap_tokens)
    age = geological_age_for(layer_version)

    chunks = [
        Chunk(
            chunk_id=compute_chunk_id(doc.doc_id, ordinal, content),
            doc_id=doc.doc_id,
            source_id=doc.source_id,
            ordinal=ordinal,
            content=content,
            char_count=len(content),
            layer_version=layer_version,
            geological_age=age,
            metadata={"filename": doc.filename, "tokens_est": str(estimate_tokens(content))},
        )
        for ordinal, content in enumerate(pieces)
    ]
    log.info(
        "event=document_chunked doc_id=%s chunks=%d layer_version=%d max_tokens=%d overlap=%d",
        doc.doc_id[:12],
        len(chunks),
        layer_version,
        max_tokens,
        overlap_tokens,
    )
    return chunks
