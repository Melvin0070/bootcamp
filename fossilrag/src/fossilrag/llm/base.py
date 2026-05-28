"""LLM provider interface + the shared grounded-prompt builder.

``/mutate`` works at the "summarise/edit, grounded in retrieved fossils" level,
so the interface is :meth:`summarise(query, instruction, hits)` rather than a
raw-prompt call. The system/user prompt is built once here so every real
provider (Bedrock, Anthropic) grounds identically; only the mock bypasses it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from fossilrag.models import ChatMessage, ExcavateHit

SYSTEM_PROMPT = (
    "You are FossilRAG, a precise document-enrichment assistant for the "
    "Dinosaur Whisperer. Answer ONLY from the provided fossil context; if the "
    "context is insufficient, say so. Be concise and factual."
)

EDIT_SYSTEM_PROMPT = (
    "You are FossilRAG's slide editor. Apply the user's instruction to the slide "
    "text and return ONLY the revised slide text — no preamble, no explanation."
)


def build_edit_prompt(text: str, instruction: str) -> tuple[str, str]:
    """Return ``(system, user)`` for an editing instruction over slide text."""
    user = f"Instruction: {instruction}\n\nSlide text to revise:\n{text}"
    return EDIT_SYSTEM_PROMPT, user


@dataclass(frozen=True)
class LLMResult:
    text: str
    model_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0  # Bedrock native prompt-cache hits (not Fossilization)
    extra: dict = field(default_factory=dict)


def build_prompt(query: str, instruction: str | None, hits: list[ExcavateHit]) -> tuple[str, str]:
    """Return ``(system, user)`` text grounding the task in the retrieved fossils."""
    context = (
        "\n".join(f"- [{h.geological_age}] {h.content}" for h in hits) or "(no fossils retrieved)"
    )
    task = instruction or "Summarise the relevant fossils for the query."
    user = f"Fossil context:\n{context}\n\nTask: {task}\nQuery: {query}"
    return SYSTEM_PROMPT, user


def build_chat(messages: list[ChatMessage], hits: list[ExcavateHit]) -> tuple[str, list[dict]]:
    """Return ``(system, turns)`` for a multi-turn chat grounded in the fossils.

    The retrieved fossils go into the system prompt (shared across turns); the
    conversation history is passed as native role/content turns so a real
    provider sees the full dialogue.
    """
    context = (
        "\n".join(f"- [{h.geological_age}] {h.content}" for h in hits) or "(no fossils retrieved)"
    )
    system = f"{SYSTEM_PROMPT}\n\nFossil context for this conversation:\n{context}"
    turns = [{"role": m.role, "content": m.content} for m in messages]
    return system, turns


@runtime_checkable
class LLMProvider(Protocol):
    @property
    def model_id(self) -> str: ...

    def summarise(
        self, *, query: str, instruction: str | None, hits: list[ExcavateHit]
    ) -> LLMResult:
        """Generate a summary/edit grounded in ``hits``."""
        ...

    def chat(self, *, messages: list[ChatMessage], hits: list[ExcavateHit]) -> LLMResult:
        """Answer the latest user turn, grounded in ``hits``, given the dialogue."""
        ...

    def edit(self, *, text: str, instruction: str) -> LLMResult:
        """Apply an editing instruction to ``text``; return the revised text."""
        ...
