"""Automated Enrichment — extract structured markers from reports/logs.

Use case #3: ingest semi-structured text, pull out the key markers (dates,
metrics, error codes), and persist them as a structured, fossil-layer-versioned
enrichment record. Pure extraction lives in ``markers``; storage is a Postgres
table managed by the vector store; the ``/enrich`` + ``/markers`` endpoints
expose it.
"""

from fossilrag.enrichment.markers import extract_markers

__all__ = ["extract_markers"]
