"""Ingestion tests: multi-format extraction + S3 raw→silver via moto.

Extraction fixtures for PDF/PPTX are generated in-process (no committed
binaries); the libs are importorskip'd so a box missing a wheel skips rather
than errors. CI installs `.[dev]` (which includes them), so all run there.
The S3 handler/storage tests use moto — in-process, $0, no account/token.
"""

from __future__ import annotations

import io
import json
import os

import pytest

from fossilrag.ingest.extract import extract_document
from fossilrag.ingest.storage import silver_key

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


# --- content-type resolution / dispatch ----------------------------------


def test_infers_markdown_from_extension_when_octet_stream():
    doc = extract_document(
        filename="notes.md",
        data=b"# Title\n\nA paragraph about fossils.",
        content_type="application/octet-stream",
    )
    assert doc.metadata["extractor"] == "md"
    assert "paragraph about fossils" in doc.text


def test_unknown_extension_with_generic_type_raises():
    with pytest.raises(ValueError):
        extract_document(filename="mystery.bin", data=b"\x00\x01", content_type="")


def test_records_byte_count_in_metadata():
    doc = extract_document(filename="t.txt", data=b"hello", content_type="text/plain")
    assert doc.metadata["bytes"] == "5"


# --- PDF (fixture generated with fpdf2) -----------------------------------


def test_extract_pdf():
    pytest.importorskip("pypdf")
    fpdf_mod = pytest.importorskip("fpdf")

    pdf = fpdf_mod.FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=14)
    pdf.multi_cell(0, 10, "Tyrannosaurus rex roamed the late Cretaceous period.")
    data = bytes(pdf.output())

    doc = extract_document(filename="trex.pdf", data=data, content_type="application/pdf")
    assert doc.metadata["extractor"] == "pdf"
    assert "Tyrannosaurus" in doc.text


# --- PPTX (fixture generated with python-pptx) ----------------------------


def test_extract_pptx():
    pptx_mod = pytest.importorskip("pptx")

    prs = pptx_mod.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])  # title + content
    slide.shapes.title.text = "Velociraptor"
    slide.placeholders[1].text = "A sickle-shaped claw on each foot."
    buf = io.BytesIO()
    prs.save(buf)

    doc = extract_document(filename="raptor.pptx", data=buf.getvalue(), content_type=PPTX_MIME)
    assert doc.metadata["extractor"] == "pptx"
    assert "Velociraptor" in doc.text
    assert "sickle-shaped claw" in doc.text


# --- storage + Lambda handler (moto) --------------------------------------


def _silver_env(bucket: str):
    """Set silver bucket + clear the settings cache; returns a restore fn."""
    from fossilrag.config import get_settings

    prev = os.environ.get("FOSSILRAG_SILVER_BUCKET")
    prev_ep = os.environ.pop("AWS_ENDPOINT_URL", None)  # don't let it break moto
    os.environ["FOSSILRAG_SILVER_BUCKET"] = bucket
    get_settings.cache_clear()

    def restore():
        if prev is None:
            os.environ.pop("FOSSILRAG_SILVER_BUCKET", None)
        else:
            os.environ["FOSSILRAG_SILVER_BUCKET"] = prev
        if prev_ep is not None:
            os.environ["AWS_ENDPOINT_URL"] = prev_ep
        get_settings.cache_clear()

    return restore


def test_handler_extracts_raw_to_silver():
    pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    from moto import mock_aws

    from fossilrag.ingest.handler import handler

    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="raw-bucket")
        s3.create_bucket(Bucket="silver-bucket")
        # Key with a space → S3 events URL-encode it; handler must unquote_plus.
        s3.put_object(
            Bucket="raw-bucket",
            Key="docs/my report.txt",
            Body=b"First para.\r\n\r\nSecond para.",
            ContentType="text/plain",
        )
        restore = _silver_env("silver-bucket")
        try:
            event = {
                "Records": [
                    {
                        "s3": {
                            "bucket": {"name": "raw-bucket"},
                            "object": {"key": "docs/my+report.txt"},
                        }
                    }
                ]
            }
            result = handler(event)
            assert len(result["processed"]) == 1
            doc_id = result["processed"][0]["doc_id"]

            obj = s3.get_object(Bucket="silver-bucket", Key=silver_key(doc_id))
            silver = json.loads(obj["Body"].read())
            assert silver["doc_id"] == doc_id
            # CRLF normalised; provenance recorded.
            assert silver["text"] == "First para.\n\nSecond para."
            assert silver["source_uri"] == "s3://raw-bucket/docs/my report.txt"
        finally:
            restore()


def test_handler_reraises_on_failure():
    pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    from moto import mock_aws

    from fossilrag.ingest.handler import handler

    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="raw-bucket")
        s3.create_bucket(Bucket="silver-bucket")
        s3.put_object(Bucket="raw-bucket", Key="bad.bin", Body=b"\x00", ContentType="")
        restore = _silver_env("silver-bucket")
        try:
            event = {
                "Records": [
                    {"s3": {"bucket": {"name": "raw-bucket"}, "object": {"key": "bad.bin"}}}
                ]
            }
            with pytest.raises(RuntimeError):  # unsupported type → surfaced for retry/DLQ
                handler(event)
        finally:
            restore()


def test_handler_requires_silver_bucket():
    pytest.importorskip("moto")
    pytest.importorskip("boto3")
    from moto import mock_aws

    from fossilrag.config import get_settings
    from fossilrag.ingest.handler import handler

    with mock_aws():
        prev = os.environ.pop("FOSSILRAG_SILVER_BUCKET", None)
        get_settings.cache_clear()
        try:
            with pytest.raises(RuntimeError):
                handler({"Records": []})
        finally:
            if prev is not None:
                os.environ["FOSSILRAG_SILVER_BUCKET"] = prev
            get_settings.cache_clear()
