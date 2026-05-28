"""Seed the vector store by running sample documents through the full spine.

Usage::

    python -m scripts.seed                 # seeds every file in samples/
    python -m scripts.seed path/to/file.txt ...

Connects with the same config the API uses (env / .env), so after seeding you
can immediately ``GET /excavate?q=...`` against the same store.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from fossilrag.config import get_settings
from fossilrag.embedding import make_embedder
from fossilrag.logging import configure_logging, get_logger
from fossilrag.pipeline import ingest_document
from fossilrag.vectorstore import make_vector_store

log = get_logger("seed")

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"


async def _run(paths: list[Path]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    embedder = make_embedder(settings)
    store = await make_vector_store(settings)
    try:
        total = 0
        for path in paths:
            data = path.read_bytes()
            result = await ingest_document(
                store=store,
                embedder=embedder,
                filename=path.name,
                data=data,
                content_type="text/plain",
            )
            total += result.chunks_indexed
            log.info(
                "event=seeded file=%s doc_id=%s chunks=%d",
                path.name,
                result.doc_id[:12],
                result.chunks_indexed,
            )
        log.info("event=seed_complete files=%d chunks_indexed=%d", len(paths), total)
    finally:
        await store.close()


def main() -> None:
    args = sys.argv[1:]
    paths = [Path(a) for a in args] if args else sorted(SAMPLES_DIR.glob("*.txt"))
    if not paths:
        log.warning("event=seed_no_files dir=%s", SAMPLES_DIR)
        return
    asyncio.run(_run(paths))


if __name__ == "__main__":
    main()
