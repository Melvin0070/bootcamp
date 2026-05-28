"""Build fine-tuning instruction/response pairs from gold-layer fossils.

Each template is ``(instruction, response_fn)``. For a chunk it yields a record
with the instruction, the chunk content as context/input, and an extractive
response — so the corpus is deterministic and dependency-free. Two output
shapes: ``chat`` (messages list, OpenAI/Anthropic-style) and ``alpaca``
(instruction/input/output).
"""

from __future__ import annotations

import json
from collections.abc import Callable

from fossilrag.chunking.text import split_sentences
from fossilrag.llm.base import SYSTEM_PROMPT
from fossilrag.models import FossilLayerChunk


def _first_sentence(text: str) -> str:
    sents = split_sentences(text)
    return sents[0] if sents else text.strip()


# Instruction templates: key -> (instruction, response_fn(chunk) -> str).
TEMPLATES: dict[str, tuple[str, Callable[[FossilLayerChunk], str]]] = {
    "summarise": (
        "Summarise this fossil record concisely.",
        lambda c: _first_sentence(c.content),
    ),
    "age": (
        "From which geological age is this fossil layer?",
        lambda c: c.geological_age,
    ),
    "qa": (
        "What is the main fact recorded in this fossil?",
        lambda c: _first_sentence(c.content),
    ),
}


def _record(instruction: str, context: str, response: str, fmt: str) -> dict:
    if fmt == "alpaca":
        return {"instruction": instruction, "input": context, "output": response}
    if fmt == "chat":
        return {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"{instruction}\n\nFossil:\n{context}"},
                {"role": "assistant", "content": response},
            ]
        }
    raise ValueError(f"unknown dataset format {fmt!r} (use 'chat' or 'alpaca')")


def build_pairs(
    chunks: list[FossilLayerChunk],
    *,
    fmt: str = "chat",
    templates: list[str] | None = None,
) -> list[dict]:
    """Build instruction/response records for ``chunks`` × selected templates."""
    keys = templates or list(TEMPLATES)
    unknown = [k for k in keys if k not in TEMPLATES]
    if unknown:
        raise ValueError(f"unknown template(s): {unknown}; available: {sorted(TEMPLATES)}")

    records: list[dict] = []
    for chunk in chunks:
        for key in keys:
            instruction, response_fn = TEMPLATES[key]
            records.append(_record(instruction, chunk.content, response_fn(chunk), fmt))
    return records


def to_jsonl(records: list[dict]) -> str:
    """Serialise records as JSONL (one compact JSON object per line)."""
    return "\n".join(json.dumps(r, separators=(",", ":")) for r in records)
