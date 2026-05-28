"""Gold-layer persistence for chunked fossils.

Two formats behind one record shape:

  * **JSONL** — default, dependency-free; one fossil per line.
  * **Parquet** — columnar (``pyarrow``, optional/lazy); better for the
    embedding stage to scan and for analytics.

Gold artifacts are **versioned by fossil layer**: the filename embeds the
``doc_id`` and ``layer_version`` (``<doc_id>.v<layer>.<ext>``), so a re-ingest
as a new version writes a new layer beside the old one rather than clobbering
it — the substrate for Time-Travel (PR5) and Fossil Diff.
"""

from __future__ import annotations

import json
from pathlib import Path

from fossilrag.logging import get_logger
from fossilrag.models import Chunk

log = get_logger("chunking.gold")


def _record(c: Chunk) -> dict:
    """Flat, columnar-friendly record (metadata folded to a JSON string)."""
    return {
        "chunk_id": c.chunk_id,
        "doc_id": c.doc_id,
        "ordinal": c.ordinal,
        "content": c.content,
        "char_count": c.char_count,
        "layer_version": c.layer_version,
        "geological_age": c.geological_age,
        "ingested_at": c.ingested_at.isoformat(),
        "metadata_json": json.dumps(c.metadata, sort_keys=True),
    }


def gold_filename(doc_id: str, layer_version: int, fmt: str) -> str:
    return f"{doc_id}.v{layer_version}.{fmt}"


def write_jsonl(chunks: list[Chunk], path: str | Path) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(_record(c), sort_keys=True) + "\n")
    log.info("event=gold_written fmt=jsonl path=%s chunks=%d", path, len(chunks))
    return str(path)


def write_parquet(chunks: list[Chunk], path: str | Path) -> str:
    import pyarrow as pa  # lazy: optional columnar dependency
    import pyarrow.parquet as pq

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist([_record(c) for c in chunks])
    pq.write_table(table, str(path))
    log.info("event=gold_written fmt=parquet path=%s chunks=%d", path, len(chunks))
    return str(path)


def write_gold(chunks: list[Chunk], out_dir: str | Path, *, fmt: str = "jsonl") -> str:
    """Write a doc's chunks to ``<out_dir>/<doc_id>.v<layer>.<fmt>``.

    Assumes all chunks share one (doc_id, layer_version) — they do, coming from
    a single :func:`chunk_document` call.
    """
    if not chunks:
        raise ValueError("no chunks to write")
    doc_id = chunks[0].doc_id
    layer = chunks[0].layer_version
    path = Path(out_dir) / gold_filename(doc_id, layer, fmt)
    if fmt == "jsonl":
        return write_jsonl(chunks, path)
    if fmt == "parquet":
        return write_parquet(chunks, path)
    raise ValueError(f"unknown gold format {fmt!r} (use 'jsonl' or 'parquet')")
