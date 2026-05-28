"""Anthropic Messages API LLM (alternate cloud backend).

A drop-in alternative to Bedrock when calling the Anthropic API directly. The
``anthropic`` SDK is optional and imported lazily; the client is injectable for
$0 unit tests.
"""

from __future__ import annotations

from typing import Any

from fossilrag.llm.base import LLMResult, build_chat, build_edit_prompt, build_prompt
from fossilrag.logging import get_logger
from fossilrag.models import ChatMessage, ExcavateHit

log = get_logger("llm.anthropic")

DEFAULT_MODEL = "claude-sonnet-4-6"


class AnthropicLLM:
    def __init__(
        self,
        model_id: str = DEFAULT_MODEL,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        client: Any = None,
    ) -> None:
        self._model_id = model_id
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._client = client

    def _ensure_client(self):  # noqa: ANN202
        if self._client is None:
            import anthropic  # lazy, optional

            self._client = anthropic.Anthropic()
        return self._client

    @property
    def model_id(self) -> str:
        return self._model_id

    def _invoke(self, system_text: str, turns: list[dict]) -> LLMResult:
        resp = self._ensure_client().messages.create(
            model=self._model_id,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            system=system_text,
            messages=[{"role": t["role"], "content": t["content"]} for t in turns],
        )
        # Defensive: tolerate empty / non-text content blocks (return "").
        blocks = getattr(resp, "content", None) or []
        text = next((getattr(b, "text", None) for b in blocks if getattr(b, "text", None)), "")
        usage = getattr(resp, "usage", None)
        return LLMResult(
            text=text,
            model_id=self._model_id,
            input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
        )

    def summarise(
        self, *, query: str, instruction: str | None, hits: list[ExcavateHit]
    ) -> LLMResult:
        system_text, user_text = build_prompt(query, instruction, hits)
        return self._invoke(system_text, [{"role": "user", "content": user_text}])

    def chat(self, *, messages: list[ChatMessage], hits: list[ExcavateHit]) -> LLMResult:
        system_text, turns = build_chat(messages, hits)
        return self._invoke(system_text, turns)

    def edit(self, *, text: str, instruction: str) -> LLMResult:
        system_text, user_text = build_edit_prompt(text, instruction)
        return self._invoke(system_text, [{"role": "user", "content": user_text}])
