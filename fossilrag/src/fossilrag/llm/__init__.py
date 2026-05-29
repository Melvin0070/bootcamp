"""Pluggable LLM providers + Prompt Fossilization (the prompt/output cache).

One :class:`~fossilrag.llm.base.LLMProvider` interface — ``mock`` (deterministic,
$0 default), ``bedrock`` (Claude via the Converse API), ``anthropic`` (Messages
API) — and a :class:`~fossilrag.llm.cache.PromptCache` that caches successful
prompt→output pairs for instant reuse (the Prompt Fossilization mutation). Real
SDKs are imported lazily, so importing this package needs no boto3/anthropic.
"""

from fossilrag.llm.base import LLMProvider
from fossilrag.llm.cache import PromptCache, fossil_key, make_prompt_cache
from fossilrag.llm.mock import MockLLM

__all__ = [
    "LLMProvider",
    "MockLLM",
    "PromptCache",
    "fossil_key",
    "make_prompt_cache",
    "make_llm",
]


def make_llm(settings=None):  # noqa: ANN001, ANN201
    """Construct the LLM provider named by ``settings.llm_provider``."""
    from fossilrag.config import get_settings

    settings = settings or get_settings()
    provider = settings.llm_provider.lower()
    if provider == "mock":
        return MockLLM()
    if provider == "bedrock":
        from fossilrag.llm.bedrock import BedrockLLM

        return BedrockLLM(
            model_id=settings.llm_model,
            region=settings.aws_region,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
            prompt_cache=settings.bedrock_prompt_cache,
        )
    if provider == "anthropic":
        from fossilrag.llm.anthropic import AnthropicLLM

        return AnthropicLLM(
            model_id=settings.anthropic_model,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        )
    if provider == "openai":
        from fossilrag.llm.openai_compat import OpenAILLM

        return OpenAILLM(
            model_id=settings.openai_model,
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
        )
    raise ValueError(f"Unknown llm_provider={provider!r} (mock|bedrock|anthropic|openai).")
