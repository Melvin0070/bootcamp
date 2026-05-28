"""Deterministic mock LLM ($0, no keys) — the default /mutate backend.

Reuses the extractive :func:`fossilrag.mutate.mock_summarise` so the spine is
demonstrable end-to-end on /mutate with no model. PR4's real value is the
provider interface + Prompt Fossilization around it; swap ``llm_provider`` to
``bedrock`` for a real Claude summary.
"""

from __future__ import annotations

from fossilrag.llm.base import LLMResult
from fossilrag.models import ChatMessage, ExcavateHit
from fossilrag.mutate import mock_summarise

MODEL_ID = "mock-llm-v1"


class MockLLM:
    @property
    def model_id(self) -> str:
        return MODEL_ID

    def summarise(
        self, *, query: str, instruction: str | None, hits: list[ExcavateHit]
    ) -> LLMResult:
        text = mock_summarise(query, instruction, hits)
        return LLMResult(text=text, model_id=MODEL_ID, output_tokens=len(text) // 4)

    def chat(self, *, messages: list[ChatMessage], hits: list[ExcavateHit]) -> LLMResult:
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        text = mock_summarise(last_user, None, hits)
        return LLMResult(text=text, model_id=MODEL_ID, output_tokens=len(text) // 4)
