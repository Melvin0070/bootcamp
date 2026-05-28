"""AWS Bedrock LLM via the Converse API (the cloud /mutate backend).

Uses the modern **Converse** API (model-agnostic system/messages/inferenceConfig)
and a **cross-region inference profile ID** as ``modelId`` (e.g.
``us.anthropic.claude-sonnet-4-6``) — the bare foundation-model id fails
on-demand in most regions. When ``prompt_cache`` is on, a ``cachePoint`` is
appended after the static system prefix to enable Bedrock's *native* prompt
caching (distinct from FossilRAG's application-level Prompt Fossilization cache,
which caches whole outputs).

``boto3`` is the optional ``aws`` extra, imported lazily; the client is
injectable so the request/response handling is unit-tested against a fake — no
real Bedrock, no cost. (Bedrock isn't emulable at $0.)
"""

from __future__ import annotations

from typing import Any

from fossilrag.llm.base import LLMResult, build_prompt
from fossilrag.logging import get_logger
from fossilrag.models import ExcavateHit

log = get_logger("llm.bedrock")

# Cross-region inference profile (NOT the bare foundation-model id).
DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"


class BedrockLLM:
    def __init__(
        self,
        model_id: str = DEFAULT_MODEL,
        *,
        region: str = "us-east-1",
        max_tokens: int = 1024,
        temperature: float = 0.2,
        prompt_cache: bool = True,
        client: Any = None,
    ) -> None:
        self._model_id = model_id
        self._region = region
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._prompt_cache = prompt_cache
        self._client = client

    def _ensure_client(self):  # noqa: ANN202
        if self._client is None:
            import boto3  # lazy, optional

            self._client = boto3.client("bedrock-runtime", region_name=self._region)
        return self._client

    @property
    def model_id(self) -> str:
        return self._model_id

    def summarise(
        self, *, query: str, instruction: str | None, hits: list[ExcavateHit]
    ) -> LLMResult:
        system_text, user_text = build_prompt(query, instruction, hits)
        system: list[dict] = [{"text": system_text}]
        if self._prompt_cache:
            # Cache the static system prefix across requests (Bedrock native).
            system.append({"cachePoint": {"type": "default"}})

        resp = self._ensure_client().converse(
            modelId=self._model_id,
            system=system,
            messages=[{"role": "user", "content": [{"text": user_text}]}],
            inferenceConfig={"maxTokens": self._max_tokens, "temperature": self._temperature},
        )
        # Extract defensively: a stopReason=max_tokens / guardrail / reasoning-only
        # turn can yield empty or non-text content. Return "" rather than
        # IndexError/KeyError (which would surface as a misleading 502).
        blocks = resp.get("output", {}).get("message", {}).get("content", []) or []
        text = next((b["text"] for b in blocks if isinstance(b, dict) and "text" in b), "")
        usage = resp.get("usage", {})
        log.info(
            "event=bedrock_converse model=%s in=%s out=%s cache_read=%s",
            self._model_id,
            usage.get("inputTokens"),
            usage.get("outputTokens"),
            usage.get("cacheReadInputTokens"),
        )
        return LLMResult(
            text=text,
            model_id=self._model_id,
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            cache_read_tokens=usage.get("cacheReadInputTokens", 0),
        )
