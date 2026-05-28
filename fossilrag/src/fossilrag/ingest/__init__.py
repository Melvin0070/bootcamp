"""Document ingestion — extract text + provenance into the silver layer.

Handles text/markdown, PDF (pypdf), and PPTX (python-pptx). The
:func:`extract_document` entry point dispatches on content type (with
filename-extension fallback). The S3-event :func:`~fossilrag.ingest.handler.handler`
reads raw objects and writes silver-layer JSON; ``storage`` holds the S3 I/O.
"""

from fossilrag.ingest.extract import SUPPORTED_CONTENT_TYPES, extract_document

__all__ = ["extract_document", "SUPPORTED_CONTENT_TYPES"]
