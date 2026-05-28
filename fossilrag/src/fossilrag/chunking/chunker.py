"""Paragraph chunker (PR0 fidelity).

Splits a document into fossil fragments on blank-line boundaries. Each chunk
carries a content-addressed ``chunk_id``, its ordinal, the fossil-layer
version, and the derived geological-age label. PR2 replaces the splitter with
token-aware semantic chunking + overlap, but the :class:`Chunk` contract and
this function's signature stay put.
"""

from __future__ import annotations

import re

from fossilrag.logging import get_logger
from fossilrag.models import Chunk, RawDocument, compute_chunk_id, geological_age_for

log = get_logger("chunking")

# Split on one-or-more blank lines (paragraph boundary), tolerating trailing
# spaces on the blank line.
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n+")


def _paragraphs(text: str) -> list[str]:
    parts = [p.strip() for p in _PARAGRAPH_BREAK.split(text)]
    return [p for p in parts if p]


def chunk_document(doc: RawDocument, *, layer_version: int = 1) -> list[Chunk]:
    """Split a :class:`RawDocument` into ordered :class:`Chunk` fossils.

    ``layer_version`` tags every chunk so re-ingesting an updated document as a
    new layer (PR2/PR5) keeps prior layers addressable for Time-Travel queries.
    """
    age = geological_age_for(layer_version)
    chunks: list[Chunk] = []
    for ordinal, para in enumerate(_paragraphs(doc.text)):
        chunk_id = compute_chunk_id(doc.doc_id, ordinal, para)
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                doc_id=doc.doc_id,
                ordinal=ordinal,
                content=para,
                char_count=len(para),
                layer_version=layer_version,
                geological_age=age,
                metadata={"filename": doc.filename},
            )
        )
    log.info(
        "event=document_chunked doc_id=%s chunks=%d layer_version=%d",
        doc.doc_id[:12],
        len(chunks),
        layer_version,
    )
    return chunks
