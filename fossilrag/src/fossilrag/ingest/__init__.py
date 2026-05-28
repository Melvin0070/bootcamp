"""Document ingestion — extract text + provenance into the silver layer.

PR0 handles plain text only. PR1 deepens this into real PPTX/PDF/TXT
extraction (pypdf / python-pptx) triggered by S3 object-created events.
"""

from fossilrag.ingest.extract import SUPPORTED_CONTENT_TYPES, extract_document

__all__ = ["extract_document", "SUPPORTED_CONTENT_TYPES"]
