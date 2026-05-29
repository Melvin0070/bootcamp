"""The load-test stats core (pure)."""

from __future__ import annotations

from scripts.loadtest import _percentile, summarise


def test_summarise_percentiles_nearest_rank():
    stats = summarise([float(x) for x in range(1, 101)])  # 1..100
    assert stats["count"] == 100
    assert stats["min"] == 1
    assert stats["max"] == 100
    assert stats["p50"] == 50
    assert stats["p90"] == 90
    assert stats["p95"] == 95
    assert stats["p99"] == 99
    assert stats["mean"] == 50.5


def test_summarise_empty_is_all_zero():
    stats = summarise([])
    assert stats["count"] == 0
    assert stats["p99"] == 0.0


def test_summarise_single_value():
    stats = summarise([12.5])
    assert stats["p50"] == 12.5
    assert stats["p99"] == 12.5
    assert stats["max"] == 12.5


def test_percentile_empty():
    assert _percentile([], 99) == 0.0
