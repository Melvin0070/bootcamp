"""Marker extraction from semi-structured reports/logs (pure, regex-based).

Dependency-free so it runs in an enrichment Lambda with no cold-start cost.
Extracts three families the brief calls out:

  * **dates** — ISO (``2026-05-29``) and ``D/M/Y`` forms.
  * **metrics** — number+unit (``1200ms``, ``4.5%``, ``5.3 GB``) and numeric
    ``key=value`` / ``key: value`` pairs.
  * **error_codes** — ``ERR-4521`` / ``E503`` style codes, ``HTTP 503`` /
    ``status 404``, and exception names (``ConnectionError``).

Each match is a :class:`~fossilrag.models.Marker` (kind, matched text, optional
normalised value). Matches are de-duplicated per (kind, text), order preserved.
"""

from __future__ import annotations

import re

from fossilrag.models import Marker

_DATE_ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_DATE_SLASH = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
# (?<![\d/-]) stops a number from starting mid-date (e.g. the "29" of
# "2026-05-29" must not become a metric). The trailing (?=\W|$) — not \b — lets
# a unit ending in a symbol like "%" match (\b fails after "%").
_METRIC_UNIT = re.compile(
    r"(?<![\d/-])\b(\d[\d,]*(?:\.\d+)?)\s?(ms|s|secs?|mins?|hrs?|GB|MB|KB|TB|%|rps|req/s|bytes?|errors?|requests?)(?=\W|$)",
    re.IGNORECASE,
)
_KV = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(\d[\d,]*(?:\.\d+)?)\b")
_ERR_CODE = re.compile(r"\b(?:ERR(?:OR)?[-_]?\d{2,}|E\d{3,})\b")
_HTTP = re.compile(r"\b(?:HTTP|status)\s?([1-5]\d{2})\b", re.IGNORECASE)
_EXCEPTION = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:Error|Exception)\b")


def _dedup(markers: list[Marker]) -> list[Marker]:
    seen: set[tuple[str, str]] = set()
    out: list[Marker] = []
    for m in markers:
        key = (m.kind, m.text)
        if key not in seen:
            seen.add(key)
            out.append(m)
    return out


def extract_markers(text: str) -> list[Marker]:
    """Extract dates, metrics, and error codes from ``text``."""
    markers: list[Marker] = []

    for m in _DATE_ISO.finditer(text):
        markers.append(Marker(kind="date", text=m.group(0), value=m.group(0)))
    for m in _DATE_SLASH.finditer(text):
        markers.append(Marker(kind="date", text=m.group(0)))

    for m in _METRIC_UNIT.finditer(text):
        markers.append(Marker(kind="metric", text=m.group(0).strip(), value=m.group(1)))
    for m in _KV.finditer(text):
        markers.append(Marker(kind="metric", text=f"{m.group(1)}={m.group(2)}", value=m.group(2)))

    for m in _ERR_CODE.finditer(text):
        markers.append(Marker(kind="error_code", text=m.group(0)))
    for m in _HTTP.finditer(text):
        markers.append(Marker(kind="error_code", text=m.group(0), value=m.group(1)))
    for m in _EXCEPTION.finditer(text):
        markers.append(Marker(kind="error_code", text=m.group(0)))

    return _dedup(markers)
