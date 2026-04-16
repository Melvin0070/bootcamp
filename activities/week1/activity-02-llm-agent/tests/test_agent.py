"""
Unit tests for the fixed LLM PowerPoint editing agent.

All tests mock the Anthropic API — no real API calls are made.
Run with:  pytest tests/ -v
"""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Set required env vars BEFORE importing the module
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key"
os.environ["PRIMARY_MODEL"] = "claude-sonnet-4-6-20250929"
os.environ["FALLBACK_MODEL"] = "claude-haiku-4-5-20251001"
os.environ["SLIDE_TIMEOUT"] = "2.0"
os.environ["MAX_CONCURRENT"] = "5"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_slides(*contents: str) -> list[dict]:
    return [{"number": i + 1, "content": c} for i, c in enumerate(contents)]


def _mock_response(text: str, cache_read: int = 0, cache_write: int = 0):
    """Build a mock Anthropic API response."""
    usage = MagicMock()
    usage.input_tokens = 500
    usage.output_tokens = 200
    usage.cache_read_input_tokens = cache_read
    usage.cache_creation_input_tokens = cache_write

    content_block = MagicMock()
    content_block.text = text

    response = MagicMock()
    response.content = [content_block]
    response.usage = usage
    return response


# ---------------------------------------------------------------------------
# Parallel execution
# ---------------------------------------------------------------------------

class TestParallelExecution:

    @pytest.mark.asyncio
    async def test_all_slides_processed_concurrently(self):
        """All slides should be processed — none skipped."""
        mock_create = AsyncMock(
            return_value=_mock_response('{"suggestion": "test"}')
        )

        with patch.object(agent.client.messages, "create", mock_create):
            result = await agent.edit_presentation(
                _make_slides("slide 1", "slide 2", "slide 3"),
                "Make concise",
            )

        assert result["slide_count"] == 3
        assert len(result["results"]) == 3
        assert mock_create.call_count == 3

    @pytest.mark.asyncio
    async def test_results_contain_all_slide_numbers(self):
        """Each result should carry its slide number."""
        mock_create = AsyncMock(
            return_value=_mock_response('{"suggestion": "ok"}')
        )

        with patch.object(agent.client.messages, "create", mock_create):
            result = await agent.edit_presentation(
                _make_slides("a", "b", "c", "d", "e"),
                "Edit",
            )

        slide_numbers = {r["slide_number"] for r in result["results"]}
        assert slide_numbers == {1, 2, 3, 4, 5}


# ---------------------------------------------------------------------------
# Prompt caching
# ---------------------------------------------------------------------------

class TestPromptCaching:

    @pytest.mark.asyncio
    async def test_system_prompt_uses_cache_control(self):
        """The system message must include cache_control for caching to work."""
        mock_create = AsyncMock(
            return_value=_mock_response('{"suggestion": "cached"}', cache_read=1500)
        )

        with patch.object(agent.client.messages, "create", mock_create):
            await agent.edit_presentation(
                _make_slides("test slide"),
                "Edit",
            )

        # Inspect the system= argument passed to messages.create
        call_kwargs = mock_create.call_args_list[0][1]
        system_arg = call_kwargs["system"]

        # Must be a list of blocks, not a plain string
        assert isinstance(system_arg, list)
        assert system_arg[0]["type"] == "text"
        assert system_arg[0]["cache_control"] == {"type": "ephemeral"}

    @pytest.mark.asyncio
    async def test_cache_hit_counted_in_stats(self):
        """Cache hits should be reflected in the response stats."""
        mock_create = AsyncMock(
            return_value=_mock_response('{"suggestion": "ok"}', cache_read=1500)
        )

        with patch.object(agent.client.messages, "create", mock_create):
            result = await agent.edit_presentation(
                _make_slides("a", "b"),
                "Edit",
            )

        assert result["cache_hits"] == 2
        assert all(r["cache_hit"] for r in result["results"])

    @pytest.mark.asyncio
    async def test_cache_miss_on_first_call(self):
        """First call should be a cache write, not a read."""
        mock_create = AsyncMock(
            return_value=_mock_response(
                '{"suggestion": "ok"}',
                cache_read=0,
                cache_write=1500,
            )
        )

        with patch.object(agent.client.messages, "create", mock_create):
            result = await agent.edit_presentation(
                _make_slides("first call"),
                "Edit",
            )

        assert result["cache_hits"] == 0
        assert result["results"][0]["cache_write_tokens"] == 1500


# ---------------------------------------------------------------------------
# Fallback model
# ---------------------------------------------------------------------------

class TestFallbackModel:

    @pytest.mark.asyncio
    async def test_timeout_triggers_fallback(self):
        """When primary times out, fallback should handle the request."""
        call_count = 0

        async def slow_then_fast(**kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("model") == agent.PRIMARY_MODEL:
                # Simulate slow primary — exceeds SLIDE_TIMEOUT (2s)
                await asyncio.sleep(10)
                return _mock_response("should never return")
            else:
                # Fallback returns immediately
                return _mock_response('{"suggestion": "fallback result"}')

        mock_create = AsyncMock(side_effect=slow_then_fast)

        with patch.object(agent.client.messages, "create", mock_create):
            result = await agent.edit_presentation(
                _make_slides("test"),
                "Edit",
            )

        assert result["fallback_count"] == 1
        assert result["results"][0]["used_fallback"] is True
        assert result["results"][0]["suggestion"] == '{"suggestion": "fallback result"}'

    @pytest.mark.asyncio
    async def test_api_error_triggers_fallback(self):
        """An API error on primary should trigger fallback, not crash."""
        call_count = 0

        async def error_then_ok(**kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("model") == agent.PRIMARY_MODEL:
                raise anthropic.APIStatusError(
                    message="Rate limited",
                    response=MagicMock(status_code=429),
                    body={"error": {"message": "Rate limited"}},
                )
            return _mock_response('{"suggestion": "ok from fallback"}')

        mock_create = AsyncMock(side_effect=error_then_ok)

        with patch.object(agent.client.messages, "create", mock_create):
            result = await agent.edit_presentation(
                _make_slides("test"),
                "Edit",
            )

        assert result["fallback_count"] == 1
        assert result["results"][0]["used_fallback"] is True

    @pytest.mark.asyncio
    async def test_both_models_fail_returns_error_not_crash(self):
        """If both primary AND fallback fail, return an error result, don't raise."""
        mock_create = AsyncMock(
            side_effect=anthropic.APIConnectionError(request=MagicMock())
        )

        with patch.object(agent.client.messages, "create", mock_create):
            result = await agent.edit_presentation(
                _make_slides("test"),
                "Edit",
            )

        assert result["results"][0]["suggestion"] is None
        assert "error" in result["results"][0]
        assert result["results"][0]["used_fallback"] is True


# ---------------------------------------------------------------------------
# Concurrency control
# ---------------------------------------------------------------------------

class TestConcurrencyControl:

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrent_calls(self):
        """MAX_CONCURRENT should limit how many API calls run at once."""
        max_seen = 0
        current = 0
        lock = asyncio.Lock()

        async def track_concurrency(**kwargs):
            nonlocal max_seen, current
            async with lock:
                current += 1
                if current > max_seen:
                    max_seen = current
            await asyncio.sleep(0.1)  # simulate API latency
            async with lock:
                current -= 1
            return _mock_response('{"suggestion": "ok"}')

        mock_create = AsyncMock(side_effect=track_concurrency)

        with patch.object(agent.client.messages, "create", mock_create):
            # Send 10 slides but MAX_CONCURRENT is 5
            slides = _make_slides(*[f"slide {i}" for i in range(10)])
            await agent.edit_presentation(slides, "Edit")

        # max_seen should never exceed MAX_CONCURRENT (5)
        assert max_seen <= int(os.environ["MAX_CONCURRENT"])


# ---------------------------------------------------------------------------
# Environment config
# ---------------------------------------------------------------------------

class TestConfig:

    def test_no_hardcoded_api_key(self):
        """Agent must not contain hardcoded API keys."""
        import inspect
        source = inspect.getsource(agent)
        assert "sk-ant-api03" not in source
        assert "sk-ant-" not in source.replace("sk-ant-test-key", "")

    def test_models_configurable(self):
        """Model names should come from environment, not hardcoded."""
        assert agent.PRIMARY_MODEL == os.environ["PRIMARY_MODEL"]
        assert agent.FALLBACK_MODEL == os.environ["FALLBACK_MODEL"]
