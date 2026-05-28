"""Marker extraction (pure) for Automated Enrichment."""

from __future__ import annotations

from fossilrag.enrichment import extract_markers


def test_extract_markers_from_a_log_line():
    log = (
        "2026-05-29 ERROR: request to /api failed with HTTP 503 after 1200ms; "
        "retries=3; error_rate=4.5%. Raised ConnectionError. See ERR-4521."
    )
    markers = extract_markers(log)
    kinds = {m.kind for m in markers}
    texts = {m.text for m in markers}

    assert {"date", "metric", "error_code"} <= kinds
    assert "2026-05-29" in texts  # date
    assert "HTTP 503" in texts  # http status
    assert "ConnectionError" in texts  # exception
    assert "ERR-4521" in texts  # error code
    assert "1200ms" in texts  # metric (unit)
    assert "retries=3" in texts  # metric (key=value)
    # "ERROR" (a log level, no digits) must NOT be picked up as an error code.
    assert "ERROR" not in texts


def test_metric_does_not_swallow_date_digits_and_captures_percent():
    markers = extract_markers("On 2026-05-29 the error_rate was 4.5% with 12 errors.")
    texts = {m.text for m in markers}
    metrics = {m.text for m in markers if m.kind == "metric"}
    assert "2026-05-29" in texts  # date intact
    assert not any("29" in mt and "ERROR" in mt.upper() for mt in metrics)  # no date fragment
    assert "4.5%" in metrics  # percent now captured by the unit path
    assert "12 errors" in metrics


def test_extract_markers_dedup_and_empty():
    assert extract_markers("") == []
    markers = extract_markers("HTTP 500 then HTTP 500 again")
    assert sum(1 for m in markers if m.text == "HTTP 500") == 1  # de-duplicated
