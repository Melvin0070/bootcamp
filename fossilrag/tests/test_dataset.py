"""Fine-Tuning Dataset Builder (pure) — JSONL instruction/response pairs."""

from __future__ import annotations

import json

import pytest

from fossilrag.dataset import TEMPLATES, build_pairs, to_jsonl
from fossilrag.models import FossilLayerChunk


def _chunk(content: str, age: str = "Holocene") -> FossilLayerChunk:
    return FossilLayerChunk(
        chunk_id="c",
        doc_id="d",
        source_id="s",
        ordinal=0,
        content=content,
        layer_version=1,
        geological_age=age,
    )


def test_build_pairs_chat_format():
    recs = build_pairs([_chunk("T. rex was big. It roamed.")], fmt="chat")
    assert len(recs) == len(TEMPLATES)  # one record per template
    msgs = recs[0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant"]
    assert "Fossil:" in msgs[1]["content"]


def test_build_pairs_alpaca_and_age_response():
    recs = build_pairs([_chunk("X.", age="Jurassic")], fmt="alpaca", templates=["age"])
    assert recs == [{"instruction": TEMPLATES["age"][0], "input": "X.", "output": "Jurassic"}]


def test_summarise_response_is_first_sentence():
    recs = build_pairs(
        [_chunk("First fact here. Second fact.")], fmt="alpaca", templates=["summarise"]
    )
    assert recs[0]["output"] == "First fact here."


def test_build_pairs_unknown_template_and_format_raise():
    with pytest.raises(ValueError):
        build_pairs([_chunk("x")], templates=["nope"])
    with pytest.raises(ValueError):
        build_pairs([_chunk("x")], fmt="xml")


def test_to_jsonl_is_valid_jsonl():
    recs = build_pairs([_chunk("A. B.")], fmt="alpaca", templates=["summarise"])
    lines = to_jsonl(recs).splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["output"] == "A."
