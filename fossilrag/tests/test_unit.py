"""Unit tests for the pure spine components — no database, run anywhere."""

from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from fossilrag.config import Settings
from fossilrag.embedding import make_embedder
from fossilrag.embedding.mock import MockEmbedder
from fossilrag.ingest.extract import extract_document
from fossilrag.models import (
    ExcavateHit,
    RawDocument,
    compute_chunk_id,
    compute_doc_id,
    geological_age_for,
)
from fossilrag.mutate import mock_summarise

# --- MockEmbedder --------------------------------------------------------


def test_mock_embedder_is_deterministic_and_normalised():
    a = MockEmbedder(dimensions=64)
    b = MockEmbedder(dimensions=64)
    v1 = a.encode_one("Tyrannosaurus rex")
    v2 = b.encode_one("Tyrannosaurus rex")
    assert v1.shape == (64,)
    assert v1.dtype == np.float32
    np.testing.assert_array_equal(v1, v2)  # deterministic across instances
    assert np.isclose(np.linalg.norm(v1), 1.0, atol=1e-5)  # L2-normalised


def test_mock_embedder_distinguishes_texts():
    e = MockEmbedder(dimensions=128)
    v1 = e.encode_one("allosaurus")
    v2 = e.encode_one("brontosaurus")
    # Different texts → different (and near-orthogonal) vectors.
    assert float(np.dot(v1, v2)) < 0.5


def test_mock_embedder_batch_shapes():
    e = MockEmbedder(dimensions=32)
    batch = e.encode(["a", "b", "c"])
    assert batch.shape == (3, 32)
    assert e.encode([]).shape == (0, 32)


def test_mock_embedder_rejects_tiny_dim():
    with pytest.raises(ValueError):
        MockEmbedder(dimensions=1)


def test_make_embedder_mock_and_unknown():
    s = Settings(embed_provider="mock", embed_model="m", embed_dim=16)
    e = make_embedder(s)
    assert e.dimensions == 16 and e.model_id == "m"
    with pytest.raises(ValueError):
        make_embedder(Settings(embed_provider="does-not-exist"))


# --- content-addressed identity -----------------------------------------


def test_ids_are_stable_and_collision_resistant():
    d1 = compute_doc_id("a.txt", "hello")
    d2 = compute_doc_id("a.txt", "hello")
    d3 = compute_doc_id("b.txt", "hello")
    assert d1 == d2 and d1 != d3 and len(d1) == 64

    c1 = compute_chunk_id(d1, 0, "para")
    c2 = compute_chunk_id(d1, 0, "para")
    c3 = compute_chunk_id(d1, 1, "para")  # ordinal distinguishes identical text
    assert c1 == c2 and c1 != c3


def test_geological_age_mapping():
    assert geological_age_for(1) == "Holocene"
    assert geological_age_for(0) == "Holocene"  # clamps up
    assert geological_age_for(999) == "Triassic"  # clamps to oldest


# --- extraction ----------------------------------------------------------


def test_extract_txt_and_provenance():
    doc = extract_document(filename="t.txt", data=b"Hello fossils", content_type="text/plain")
    assert isinstance(doc, RawDocument)
    assert doc.text == "Hello fossils"
    assert doc.char_count == len("Hello fossils")
    assert doc.metadata["extractor"] == "txt"


def test_extract_strips_bom_and_tolerates_bad_bytes():
    doc = extract_document(filename="b.txt", data=b"\xef\xbb\xbfcaf\xe9", content_type="text/plain")
    assert doc.text.startswith("caf")  # BOM stripped, bad byte replaced not raised


def test_extract_rejects_unsupported_type():
    with pytest.raises(ValueError):
        extract_document(filename="x.zip", data=b"PK", content_type="application/zip")


def test_extract_handles_content_type_params():
    doc = extract_document(filename="t.txt", data=b"hi", content_type="text/plain; charset=utf-8")
    assert doc.content_type == "text/plain"


def test_extract_normalises_crlf():
    # CRLF/CR are normalised to LF at the decode boundary (chunking tests in
    # test_chunking.py cover the semantic split).
    doc = extract_document(filename="win.txt", data=b"A\r\n\r\nB", content_type="text/plain")
    assert doc.text == "A\n\nB"


# --- mock summariser (/mutate, PR0) --------------------------------------


def _hit(content: str, score: float) -> ExcavateHit:
    return ExcavateHit(
        chunk_id="c",
        doc_id="d",
        ordinal=0,
        content=content,
        score=score,
        layer_version=1,
        geological_age="Holocene",
    )


def test_mock_summarise_empty():
    assert "nothing to summarise" in mock_summarise("q", None, [])


def test_mock_summarise_extracts_first_sentences():
    hits = [_hit("Alpha fact. More detail.", 0.9), _hit("Beta fact.", 0.8)]
    out = mock_summarise("q", "be concise", hits)
    assert "2 retrieved fossil" in out
    assert "be concise" in out
    assert "- Alpha fact" in out  # first sentence only (split on '. ')
    assert "More detail" not in out
    assert "- Beta fact." in out


# --- settings ------------------------------------------------------------


def test_settings_reject_inverted_pool_sizes():
    with pytest.raises(ValidationError):
        Settings(pool_min_size=20, pool_max_size=5)


def test_settings_reject_bad_ef_search():
    with pytest.raises(ValidationError):
        Settings(hnsw_ef_search=0)


def test_settings_reject_negative_pool_min():
    with pytest.raises(ValidationError):
        Settings(pool_min_size=-1)


def test_settings_reject_nonpositive_timeout():
    with pytest.raises(ValidationError):
        Settings(command_timeout_sec=0)


def test_settings_defaults():
    s = Settings()
    assert s.embed_provider == "mock"
    assert s.embed_dim == 384
    assert s.vector_table == "fossil_chunks"
