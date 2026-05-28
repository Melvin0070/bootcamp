"""Fine-Tuning Dataset Builder — JSONL instruction/response pairs from fossils.

The mutation that turns gold-layer fossils into a fine-tuning corpus: each chunk
× each instruction template becomes an (instruction, context, response) record,
emitted in the chat or alpaca JSONL shape. Responses are extractive (derived
from the chunk) so the dataset is generated deterministically at $0 — no LLM
calls — and is reproducible.
"""

from fossilrag.dataset.builder import TEMPLATES, build_pairs, to_jsonl

__all__ = ["TEMPLATES", "build_pairs", "to_jsonl"]
