"""Core domain models and content-addressed identity.

The data flows through three layers, mirroring the medallion architecture and
the project's fossil metaphor:

  * **silver** — :class:`RawDocument`: extracted text + provenance metadata.
  * **gold**   — :class:`Chunk`: a cleaned, semantically-split "fossil layer"
    fragment, ready to embed.
  * **served** — :class:`ExcavateHit`: a retrieved chunk plus its similarity
    score, returned by ``/excavate``.

Identity is **content-addressed**: ``doc_id`` and ``chunk_id`` are SHA-256
digests of the content (plus discriminators). Re-ingesting the same bytes
yields the same ids, which is what makes the whole pipeline idempotent — the
same paragraph is never embedded or indexed twice (the Self-Healing
Idempotency mutation builds directly on this).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


def compute_doc_id(filename: str, content: str) -> str:
    """Stable id for a document: sha256 over filename + content."""
    h = hashlib.sha256()
    h.update(filename.encode("utf-8"))
    h.update(b"\x00")
    h.update(content.encode("utf-8"))
    return h.hexdigest()


def compute_chunk_id(doc_id: str, ordinal: int, text: str) -> str:
    """Stable id for a chunk within a document.

    Includes the ordinal so two identical paragraphs in the same document
    remain distinct fossils, while the same chunk re-derived on a re-run keeps
    its id (idempotent).
    """
    h = hashlib.sha256()
    h.update(doc_id.encode("utf-8"))
    h.update(f"\x00{ordinal}\x00".encode())
    h.update(text.encode("utf-8"))
    return h.hexdigest()


# Geological eras, newest → oldest. ``layer_version`` 1 is the most recent
# excavation; higher versions are deeper (older) fossil layers. This is the
# flavour metadata the brief calls "geological age"; Time-Travel Query (PR5)
# uses ``layer_version`` to address a specific layer.
_ERAS = [
    "Holocene",
    "Pleistocene",
    "Pliocene",
    "Miocene",
    "Oligocene",
    "Eocene",
    "Paleocene",
    "Cretaceous",
    "Jurassic",
    "Triassic",
]


def geological_age_for(layer_version: int) -> str:
    """Map a fossil-layer version to a geological era label (flavour metadata)."""
    if layer_version < 1:
        layer_version = 1
    return _ERAS[min(layer_version - 1, len(_ERAS) - 1)]


class RawDocument(BaseModel):
    """Silver-layer record: extracted text + provenance."""

    doc_id: str
    # Stable logical-document identity ACROSS content versions (defaults to the
    # filename). doc_id is the content hash and changes when the bytes change;
    # source_id stays put, so versions of "report.txt" share a source_id and are
    # addressable as fossil layers (Time-Travel / Fossil Diff).
    source_id: str
    filename: str
    content_type: str = "text/plain"
    text: str
    char_count: int
    user_id: str | None = None
    source_uri: str | None = None
    uploaded_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_text(
        cls,
        *,
        filename: str,
        text: str,
        content_type: str = "text/plain",
        user_id: str | None = None,
        source_uri: str | None = None,
        source_id: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> RawDocument:
        return cls(
            doc_id=compute_doc_id(filename, text),
            source_id=source_id or filename,
            filename=filename,
            content_type=content_type,
            text=text,
            char_count=len(text),
            user_id=user_id,
            source_uri=source_uri,
            metadata=metadata or {},
        )


class Chunk(BaseModel):
    """Gold-layer fossil fragment, ready for embedding."""

    chunk_id: str
    doc_id: str
    source_id: str = ""
    ordinal: int
    content: str
    char_count: int
    layer_version: int = 1
    geological_age: str = Field(default_factory=lambda: geological_age_for(1))
    ingested_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, str] = Field(default_factory=dict)


class ExcavateHit(BaseModel):
    """A retrieved chunk plus its similarity score (served layer)."""

    chunk_id: str
    doc_id: str
    source_id: str = ""
    ordinal: int
    content: str
    score: float = Field(description="Cosine similarity in [-1, 1]; higher is closer.")
    layer_version: int
    geological_age: str
    metadata: dict[str, str] = Field(default_factory=dict)


class IngestResult(BaseModel):
    """Summary returned by the ingestion pipeline / ``POST /ingest``."""

    doc_id: str
    filename: str
    chunks_total: int
    chunks_indexed: int
    layer_version: int
    embed_model: str
    embed_dim: int


class ExcavateResponse(BaseModel):
    query: str
    k: int
    hits: list[ExcavateHit]
    latency_ms: float


class MutateResponse(BaseModel):
    """Result of ``/mutate``: a summary/edit grounded in retrieved fossils.

    ``mock`` is True while the LLM is the PR0 placeholder; PR4 swaps in AWS
    Bedrock (Converse API) + Prompt Fossilization behind a provider interface
    and flips it to False.
    """

    query: str
    instruction: str | None = None
    summary: str
    model_id: str
    mock: bool
    cached: bool = False
    used_chunks: list[ExcavateHit]
    note: str = ""
    latency_ms: float


class FossilLayerChunk(BaseModel):
    """One chunk of a specific fossil layer (served without its embedding)."""

    chunk_id: str
    doc_id: str
    source_id: str
    ordinal: int
    content: str
    layer_version: int
    geological_age: str


class TimeTravelResponse(BaseModel):
    """A document's fossils at a chosen layer + how it relates to the latest."""

    source_id: str
    version: int
    latest_version: int
    available_versions: list[int]
    is_latest: bool
    chunks: list[FossilLayerChunk]


class FossilDiffResponse(BaseModel):
    """Changes between two fossil layers of the same document."""

    source_id: str
    from_version: int
    to_version: int
    changed: bool
    added_lines: int
    removed_lines: int
    unified_diff: str


class Marker(BaseModel):
    """A structured marker extracted from a document (Automated Enrichment)."""

    kind: str  # date | metric | error_code
    text: str
    value: str | None = None


class EnrichmentRecord(BaseModel):
    """A document layer's extracted markers — the structured enrichment record."""

    source_id: str
    doc_id: str
    layer_version: int
    markers: list[Marker]
    counts: dict[str, int]


class ChatMessage(BaseModel):
    """One turn of a Chat-Based Fossil Excavation conversation."""

    role: str  # user | assistant
    content: str = Field(min_length=1)


class ChatResponse(BaseModel):
    """Assistant answer grounded in retrieved fossils, with citations."""

    answer: str
    model_id: str
    mock: bool
    cached: bool = False
    citations: list[ExcavateHit]
    latency_ms: float
