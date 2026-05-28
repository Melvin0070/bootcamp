"""Text extraction → silver-layer :class:`RawDocument`.

PR0 scope: plain text (``.txt`` / ``text/plain``). The signature already takes
raw ``bytes`` + a ``content_type`` so PR1 can add PDF/PPTX branches without
changing any caller — the ingestion Lambda, the ``POST /ingest`` handler, and
the seed script all go through :func:`extract_document`.
"""

from __future__ import annotations

from fossilrag.logging import get_logger
from fossilrag.models import RawDocument

log = get_logger("ingest")

# Content types this build can extract. PR1 extends with application/pdf and
# the PPTX media type.
SUPPORTED_CONTENT_TYPES: dict[str, str] = {
    "text/plain": "txt",
    "text/markdown": "md",
}


def _decode_text(data: bytes) -> str:
    """Decode bytes as UTF-8 and normalise line endings.

    - ``utf-8-sig`` strips a BOM if present; ``errors="replace"`` keeps
      ingestion robust to the occasional mis-encoded byte rather than failing a
      whole document.
    - CRLF (Windows) and bare CR (old Mac) are normalised to LF so paragraph
      splitting — which keys on blank LF lines — works on uploaded documents
      regardless of origin OS, not just Unix ones. (Deeper cleaning /
      whitespace normalisation lands in PR2.)
    """
    text = data.decode("utf-8-sig", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def extract_document(
    *,
    filename: str,
    data: bytes,
    content_type: str = "text/plain",
    user_id: str | None = None,
    source_uri: str | None = None,
) -> RawDocument:
    """Extract text + metadata from raw bytes into a :class:`RawDocument`.

    Raises ``ValueError`` for unsupported content types so the caller returns a
    clean 4xx rather than indexing garbage.
    """
    ctype = content_type.split(";", 1)[0].strip().lower()
    if ctype not in SUPPORTED_CONTENT_TYPES:
        raise ValueError(
            f"Unsupported content_type {content_type!r}. "
            f"Supported in this build: {sorted(SUPPORTED_CONTENT_TYPES)}."
        )

    text = _decode_text(data)
    doc = RawDocument.from_text(
        filename=filename,
        text=text,
        content_type=ctype,
        user_id=user_id,
        source_uri=source_uri,
        metadata={"extractor": SUPPORTED_CONTENT_TYPES[ctype]},
    )
    log.info(
        "event=document_extracted doc_id=%s filename=%s content_type=%s chars=%d",
        doc.doc_id[:12],
        filename,
        ctype,
        doc.char_count,
    )
    return doc
