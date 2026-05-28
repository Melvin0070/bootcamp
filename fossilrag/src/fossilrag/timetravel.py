"""Fossil Diff — compute changes between two fossil layers (pure, no I/O).

Layer retrieval lives in the vector store; this module just diffs two ordered
chunk lists with the stdlib ``difflib``, so it's trivially unit-tested. Powers
the ``/diff`` endpoint (Fossil Diff mutation); Time-Travel (``/timetravel``)
uses the store's layer queries directly.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from fossilrag.models import FossilLayerChunk


@dataclass(frozen=True)
class DiffResult:
    unified_diff: str
    added_lines: int
    removed_lines: int
    changed: bool


def unified_text_diff(
    from_lines: list[str], to_lines: list[str], *, from_label: str, to_label: str
) -> DiffResult:
    """Unified diff over two line lists, with added/removed counts."""
    diff_lines = list(
        difflib.unified_diff(
            from_lines, to_lines, fromfile=from_label, tofile=to_label, lineterm=""
        )
    )
    # Exclude difflib's "+++"/"---" file headers from the change counts. (A line
    # whose content literally starts with "++"/"--" would be miscounted, but
    # never appears in the diff text itself — cleaned prose, effectively
    # unreachable.)
    added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))
    return DiffResult(
        unified_diff="\n".join(diff_lines),
        added_lines=added,
        removed_lines=removed,
        changed=bool(added or removed),
    )


def unified_fossil_diff(
    from_version: int,
    to_version: int,
    from_chunks: list[FossilLayerChunk],
    to_chunks: list[FossilLayerChunk],
) -> DiffResult:
    """Unified diff of two layers' chunk contents (one chunk per diff line)."""
    return unified_text_diff(
        [c.content for c in from_chunks],
        [c.content for c in to_chunks],
        from_label=f"v{from_version}",
        to_label=f"v{to_version}",
    )
