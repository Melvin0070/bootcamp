"""Fossil Diff logic (pure) — exercised without a database."""

from __future__ import annotations

from fossilrag.models import FossilLayerChunk
from fossilrag.timetravel import unified_fossil_diff


def _layer(contents: list[str], version: int) -> list[FossilLayerChunk]:
    return [
        FossilLayerChunk(
            chunk_id=f"c{i}",
            doc_id="d",
            source_id="s",
            ordinal=i,
            content=c,
            layer_version=version,
            geological_age="Holocene",
        )
        for i, c in enumerate(contents)
    ]


def test_unified_fossil_diff_detects_changes():
    a = _layer(["alpha", "beta", "gamma"], 1)
    b = _layer(["alpha", "BETA", "gamma", "delta"], 2)
    d = unified_fossil_diff(1, 2, a, b)
    assert d.changed is True
    assert d.added_lines >= 1 and d.removed_lines >= 1
    assert "v1" in d.unified_diff and "v2" in d.unified_diff
    assert "+delta" in d.unified_diff
    assert "-beta" in d.unified_diff


def test_unified_fossil_diff_identical_layers_no_change():
    a = _layer(["x", "y"], 1)
    b = _layer(["x", "y"], 2)
    d = unified_fossil_diff(1, 2, a, b)
    assert d.changed is False
    assert d.added_lines == 0 and d.removed_lines == 0
    assert d.unified_diff == ""
