"""Tests for cleaning + semantic token-aware chunking + gold-layer writers."""

from __future__ import annotations

import json
import re

import pytest

from fossilrag.chunking.chunker import chunk_document, chunk_text
from fossilrag.chunking.gold import write_gold
from fossilrag.chunking.text import clean_text, estimate_tokens, split_sentences
from fossilrag.models import RawDocument, compute_chunk_id, geological_age_for

# --- cleaning -------------------------------------------------------------


def test_clean_text_strips_control_and_collapses_whitespace():
    raw = "a\x00b   c\r\n\r\n\r\nd"
    assert clean_text(raw) == "ab c\n\nd"


def test_clean_text_empty():
    assert clean_text("  \x00 \n\n ") == ""


def test_clean_text_is_nfc_idempotent():
    # A control char between a base letter and a combining mark must not block
    # NFC composition — else clean_text isn't idempotent and two inputs that
    # are the same fossil would hash to different content-addressed ids.
    raw = "e\x00́ fossil"  # 'e' + NUL + COMBINING ACUTE ACCENT
    once = clean_text(raw)
    assert once == clean_text(once)  # idempotent
    assert once == clean_text("é fossil")  # precomposed 'é' → same cleaned text


# --- sentence segmentation + token estimate -------------------------------


def test_split_sentences():
    assert split_sentences("First. Second! Third?") == ["First.", "Second!", "Third?"]
    assert split_sentences("No punctuation here") == ["No punctuation here"]
    assert split_sentences("   ") == []


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 8) == 2


# --- semantic chunking ----------------------------------------------------


def _markers(chunk: str) -> set[str]:
    return set(re.findall(r"Sentence\d+", chunk))


def test_chunk_text_packs_with_overlap():
    text = " ".join(f"Sentence{i} alpha bravo charlie delta." for i in range(8))
    chunks = chunk_text(text, max_tokens=20, overlap_tokens=8)
    assert len(chunks) > 1
    # Consecutive chunks share at least one sentence (overlap working).
    assert any(_markers(chunks[i]) & _markers(chunks[i + 1]) for i in range(len(chunks) - 1))


def test_chunk_text_no_overlap_has_disjoint_neighbours():
    text = " ".join(f"Sentence{i} alpha bravo charlie delta." for i in range(8))
    chunks = chunk_text(text, max_tokens=20, overlap_tokens=0)
    assert len(chunks) > 1
    assert all(not (_markers(chunks[i]) & _markers(chunks[i + 1])) for i in range(len(chunks) - 1))


def test_chunk_text_empty():
    assert chunk_text("", max_tokens=64, overlap_tokens=8) == []
    assert chunk_text("   \n\n  ", max_tokens=64, overlap_tokens=8) == []


def test_chunk_text_oversized_single_segment_is_one_chunk():
    text = "word " * 100  # no sentence punctuation → one big segment
    chunks = chunk_text(text.strip(), max_tokens=10, overlap_tokens=0)
    assert len(chunks) == 1


def test_chunk_text_clamps_overlap_below_max_to_progress():
    # overlap >= max must not stall; it is clamped so chunking still progresses.
    text = " ".join(f"Sentence{i} alpha bravo charlie delta." for i in range(8))
    chunks = chunk_text(text, max_tokens=20, overlap_tokens=999)
    assert len(chunks) > 1


# --- chunk_document -------------------------------------------------------


def test_chunk_document_idempotent_and_tagged():
    doc = RawDocument.from_text(
        filename="d.txt", text="Alpha one. Beta two. Gamma three. Delta four. Epsilon five."
    )
    c1 = chunk_document(doc, max_tokens=8, overlap_tokens=0)
    c2 = chunk_document(doc, max_tokens=8, overlap_tokens=0)
    assert len(c1) > 1
    assert [c.chunk_id for c in c1] == [c.chunk_id for c in c2]  # idempotent
    for c in c1:
        assert c.chunk_id == compute_chunk_id(doc.doc_id, c.ordinal, c.content)
        assert "tokens_est" in c.metadata
    assert c1[0].layer_version == 1
    assert c1[0].geological_age == "Holocene"


def test_chunk_document_tags_layer_version():
    doc = RawDocument.from_text(filename="d.txt", text="Only one sentence here.")
    chunks = chunk_document(doc, layer_version=3, max_tokens=256, overlap_tokens=0)
    assert chunks[0].layer_version == 3
    assert chunks[0].geological_age == geological_age_for(3)


def test_chunk_document_cleans_before_chunking():
    doc = RawDocument.from_text(filename="d.txt", text="Messy\x00   text   here.")
    chunks = chunk_document(doc, max_tokens=256, overlap_tokens=0)
    assert chunks[0].content == "Messy text here."


def test_chunk_document_blank_is_empty():
    doc = RawDocument.from_text(filename="b.txt", text="   \x00  \n\n ")
    assert chunk_document(doc, max_tokens=64, overlap_tokens=0) == []


# --- gold writers ---------------------------------------------------------


def _chunks():
    doc = RawDocument.from_text(filename="d.txt", text="Alpha one. Beta two. Gamma three.")
    return chunk_document(doc, max_tokens=8, overlap_tokens=0)


def test_write_gold_jsonl(tmp_path):
    chunks = _chunks()
    path = write_gold(chunks, tmp_path, fmt="jsonl")
    with open(path, encoding="utf-8") as f:
        lines = [json.loads(line) for line in f]
    assert len(lines) == len(chunks)
    assert {line["chunk_id"] for line in lines} == {c.chunk_id for c in chunks}
    # versioned filename embeds doc_id + layer
    assert path.endswith(f"{chunks[0].doc_id}.v1.jsonl")


def test_write_gold_parquet(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    chunks = _chunks()
    path = write_gold(chunks, tmp_path, fmt="parquet")
    table = pq.read_table(path)
    assert table.num_rows == len(chunks)
    assert "content" in table.column_names


def test_write_gold_rejects_unknown_format_and_empty(tmp_path):
    with pytest.raises(ValueError):
        write_gold(_chunks(), tmp_path, fmt="xml")
    with pytest.raises(ValueError):
        write_gold([], tmp_path, fmt="jsonl")
