"""OpenAI-compatible Chat Completions LLM provider.

One provider, many vendors: by pointing ``base_url`` at the right endpoint this
drives **OpenAI**, **Google Gemini** (its OpenAI-compatibility endpoint,
``https://generativelanguage.googleapis.com/v1beta/openai/``), **Ollama**
(``http://localhost:11434/v1``), OpenRouter, etc. The ``openai`` SDK is the
optional ``openai`` extra, imported lazily; the client is **injectable**
(``client=``) so request-building and response-parsing are unit-tested against a
tiny fake — no network, no key, no cost.
"""

from __future__ import annotations

from typing import Any

from fossilrag.llm.base import LLMResult, build_chat, build_edit_prompt, build_prompt
from fossilrag.logging import get_logger
from fossilrag.models import ChatMessage, ExcavateHit

log = get_logger("llm.openai")

# Verify current models at platform.openai.com/docs/models (or ai.google.dev for
# Gemini). May 2026: OpenAI gpt-5.4-mini / gpt-5-mini; Gemini gemini-2.5-flash.
DEFAULT_MODEL = "gpt-5-mini"


class OpenAILLM:
    """Chat-completions LLM over any OpenAI-compatible endpoint."""

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        client: Any = None,
    ) -> None:
        self._model_id = model_id
        self._base_url = base_url
        self._api_key = api_key
        self._client = client

    def _ensure_client(self):  # noqa: ANN202
        if self._client is None:
            from openai import OpenAI  # lazy, optional ('openai' extra)

            # api_key=None → the SDK reads OPENAI_API_KEY; base_url=None → OpenAI.
            self._client = OpenAI(base_url=self._base_url, api_key=self._api_key)
        return self._client

    @property
    def model_id(self) -> str:
        return self._model_id

    def _invoke(self, system_text: str, turns: list[dict]) -> LLMResult:
        messages = [{"role": "system", "content": system_text}]
        messages += [{"role": t["role"], "content": t["content"]} for t in turns]
        # Send ONLY model + messages — the maximally portable request. Current
        # reasoning models (OpenAI gpt-5.x, o-series) reject `max_tokens` (they
        # want `max_completion_tokens`) and reject a non-default `temperature`,
        # while Gemini's compat endpoint expects `max_tokens`; omitting both lets
        # one provider work across OpenAI, Gemini, and Ollama unchanged. Output
        # length is bounded by the "be concise" system prompt.
        resp = self._ensure_client().chat.completions.create(
            model=self._model_id,
            messages=messages,
        )
        # Defensive parse: tolerate an empty/missing choice or content.
        choices = getattr(resp, "choices", None) or []
        message = getattr(choices[0], "message", None) if choices else None
        text = (getattr(message, "content", None) or "").strip()
        usage = getattr(resp, "usage", None)
        return LLMResult(
            text=text,
            model_id=self._model_id,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0 if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0 if usage else 0,
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
