"""LLM providers (injected fakes, $0) + Prompt Fossilization cache."""

from __future__ import annotations

import pytest

from fossilrag.config import Settings
from fossilrag.llm import fossil_key, make_llm, make_prompt_cache
from fossilrag.llm.base import (
    EDIT_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_chat,
    build_edit_prompt,
    build_prompt,
)
from fossilrag.llm.bedrock import DEFAULT_MODEL as BEDROCK_MODEL
from fossilrag.llm.bedrock import BedrockLLM
from fossilrag.llm.cache import (
    DynamoDBPromptCache,
    InMemoryPromptCache,
    LocalFilePromptCache,
)
from fossilrag.llm.mock import MockLLM
from fossilrag.models import ChatMessage, ExcavateHit


def _hit(content: str, cid: str = "c1") -> ExcavateHit:
    return ExcavateHit(
        chunk_id=cid,
        doc_id="d",
        ordinal=0,
        content=content,
        score=0.9,
        layer_version=1,
        geological_age="Holocene",
    )


# --- prompt builder + mock ------------------------------------------------


def test_build_prompt_grounds_in_hits():
    system, user = build_prompt("q", "be concise", [_hit("T. rex was big.")])
    assert system == SYSTEM_PROMPT
    assert "T. rex was big." in user
    assert "be concise" in user
    assert user.rstrip().endswith("Query: q")


def test_mock_llm_summarises_from_hits():
    r = MockLLM().summarise(query="q", instruction=None, hits=[_hit("Stegosaurus had plates.")])
    assert r.model_id == "mock-llm-v1"
    assert "plates" in r.text


def test_chat_message_rejects_invalid_role():
    import pytest as _pytest
    from pydantic import ValidationError

    with _pytest.raises(ValidationError):
        ChatMessage(role="system", content="x")  # only user|assistant allowed → 422 at the API


def test_build_chat_grounds_context_and_maps_turns():
    msgs = [
        ChatMessage(role="user", content="What plates?"),
        ChatMessage(role="assistant", content="Bony ones."),
        ChatMessage(role="user", content="How many rows?"),
    ]
    system, turns = build_chat(msgs, [_hit("Stegosaurus had two rows of plates.")])
    assert "Stegosaurus had two rows" in system  # fossil context lives in the system prompt
    assert turns == [
        {"role": "user", "content": "What plates?"},
        {"role": "assistant", "content": "Bony ones."},
        {"role": "user", "content": "How many rows?"},
    ]


def test_mock_llm_chat_answers_from_last_user_turn():
    r = MockLLM().chat(
        messages=[
            ChatMessage(role="user", content="hi"),
            ChatMessage(role="assistant", content="hello"),
            ChatMessage(role="user", content="tell me about the plates"),
        ],
        hits=[_hit("Stegosaurus plates.")],
    )
    assert r.model_id == "mock-llm-v1" and "plates" in r.text


def test_build_edit_prompt():
    system, user = build_edit_prompt("Old slide text.", "make concise")
    assert system == EDIT_SYSTEM_PROMPT
    assert "make concise" in user and "Old slide text." in user


def test_mock_llm_edit_is_concise_and_deterministic():
    text = "First point here. Second point here. Third point now. Fourth point too."
    r1 = MockLLM().edit(text=text, instruction="make concise")
    r2 = MockLLM().edit(text=text, instruction="make concise")
    assert r1.text == r2.text  # deterministic
    assert 0 < len(r1.text) < len(text)  # shorter (keeps first half of sentences)
    assert r1.model_id == "mock-llm-v1"


def test_bedrock_edit_uses_edit_system_prompt():
    fake = _FakeConverse()
    BedrockLLM(client=fake).edit(text="slide text here", instruction="shorten it")
    assert fake.kwargs["system"][0]["text"] == EDIT_SYSTEM_PROMPT
    user_text = fake.kwargs["messages"][0]["content"][0]["text"]
    assert "slide text here" in user_text and "shorten it" in user_text


def test_bedrock_chat_sends_turns_and_context():
    fake = _FakeConverse()
    BedrockLLM(client=fake).chat(
        messages=[
            ChatMessage(role="user", content="hi"),
            ChatMessage(role="assistant", content="hey"),
            ChatMessage(role="user", content="about the context?"),
        ],
        hits=[_hit("ctx-fossil")],
    )
    assert [m["role"] for m in fake.kwargs["messages"]] == ["user", "assistant", "user"]
    assert "ctx-fossil" in fake.kwargs["system"][0]["text"]


# --- Bedrock (fake Converse client) ---------------------------------------


class _FakeConverse:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def converse(self, **kwargs):  # noqa: ANN003
        self.kwargs = kwargs
        return {
            "output": {"message": {"content": [{"text": "FAKE SUMMARY"}]}},
            "usage": {"inputTokens": 10, "outputTokens": 5, "cacheReadInputTokens": 3},
        }


def test_bedrock_llm_request_and_parse():
    fake = _FakeConverse()
    r = BedrockLLM(client=fake, prompt_cache=True).summarise(
        query="q", instruction="edit", hits=[_hit("ctx-fossil")]
    )
    assert r.text == "FAKE SUMMARY" and r.model_id == BEDROCK_MODEL
    assert (r.input_tokens, r.output_tokens, r.cache_read_tokens) == (10, 5, 3)
    k = fake.kwargs
    assert k["modelId"] == BEDROCK_MODEL
    assert k["system"][0]["text"] == SYSTEM_PROMPT
    assert k["system"][-1] == {"cachePoint": {"type": "default"}}  # native cache on
    assert k["messages"][0]["role"] == "user"
    assert "ctx-fossil" in k["messages"][0]["content"][0]["text"]
    assert k["inferenceConfig"]["maxTokens"] >= 1


def test_bedrock_llm_no_cachepoint_when_disabled():
    fake = _FakeConverse()
    BedrockLLM(client=fake, prompt_cache=False).summarise(
        query="q", instruction=None, hits=[_hit("x")]
    )
    assert all("cachePoint" not in block for block in fake.kwargs["system"])


def test_bedrock_llm_tolerates_empty_content():
    class _Empty:
        def converse(self, **k):  # noqa: ANN003
            return {"output": {"message": {"content": []}}, "usage": {}}

    r = BedrockLLM(client=_Empty()).summarise(query="q", instruction=None, hits=[_hit("x")])
    assert r.text == ""  # graceful empty summary, not IndexError → 502


# --- Anthropic (fake Messages client) -------------------------------------


def test_anthropic_llm_request_and_parse():
    from fossilrag.llm.anthropic import DEFAULT_MODEL, AnthropicLLM

    class _Content:
        def __init__(self, text):  # noqa: ANN001
            self.text = text

    class _Resp:
        content = [_Content("ANT SUMMARY")]
        usage = None

    class _Messages:
        def __init__(self, outer):  # noqa: ANN001
            self.outer = outer

        def create(self, **kwargs):  # noqa: ANN003
            self.outer.kwargs = kwargs
            return _Resp()

    class _FakeAnthropic:
        def __init__(self):
            self.kwargs = None
            self.messages = _Messages(self)

    fake = _FakeAnthropic()
    r = AnthropicLLM(client=fake).summarise(query="q", instruction=None, hits=[_hit("ctx")])
    assert r.text == "ANT SUMMARY" and r.model_id == DEFAULT_MODEL
    assert fake.kwargs["system"] == SYSTEM_PROMPT
    assert "ctx" in fake.kwargs["messages"][0]["content"]

    # chat path: turns mapped, fossil context grounded in the system prompt
    fake2 = _FakeAnthropic()
    AnthropicLLM(client=fake2).chat(
        messages=[
            ChatMessage(role="user", content="hi"),
            ChatMessage(role="assistant", content="yo"),
            ChatMessage(role="user", content="more"),
        ],
        hits=[_hit("ctx2")],
    )
    assert [m["role"] for m in fake2.kwargs["messages"]] == ["user", "assistant", "user"]
    assert "ctx2" in fake2.kwargs["system"]


# --- fossil_key (Prompt Fossilization key) --------------------------------


def test_fossil_key_deterministic_and_sensitive():
    hits = [_hit("a", "c1"), _hit("b", "c2")]
    k = fossil_key("m", "q", "i", hits)
    assert k == fossil_key("m", "q", "i", hits) and len(k) == 64
    assert fossil_key("m", "q2", "i", hits) != k  # query
    assert fossil_key("m", "q", "i", [_hit("a", "c1")]) != k  # hits
    assert fossil_key("m2", "q", "i", hits) != k  # model
    assert fossil_key("m", "q", None, hits) != k  # instruction


def test_fossil_key_changes_with_geological_age():
    # Same chunk_id but a re-ingested layer changed the age → different key, so
    # a stale (wrong-age) cached summary is never served.
    old = ExcavateHit(
        chunk_id="c1",
        doc_id="d",
        ordinal=0,
        content="x",
        score=0.9,
        layer_version=1,
        geological_age="Holocene",
    )
    new = ExcavateHit(
        chunk_id="c1",
        doc_id="d",
        ordinal=0,
        content="x",
        score=0.9,
        layer_version=8,
        geological_age="Cretaceous",
    )
    assert fossil_key("m", "q", None, [old]) != fossil_key("m", "q", None, [new])


# --- caches ---------------------------------------------------------------


def test_inmemory_cache():
    c = InMemoryPromptCache()
    assert c.get("k") is None
    c.put("k", "v")
    assert c.get("k") == "v"


def test_inmemory_cache_lru_eviction():
    c = InMemoryPromptCache(max_entries=2)
    c.put("a", "1")
    c.put("b", "2")
    c.put("c", "3")  # over cap → evict least-recently-used ("a")
    assert c.get("a") is None
    assert c.get("b") == "2" and c.get("c") == "3"


def test_local_file_cache_persists(tmp_path):
    path = tmp_path / "pc.json"
    LocalFilePromptCache(path).put("k", "v")
    assert LocalFilePromptCache(path).get("k") == "v"  # survives a fresh instance


def test_make_prompt_cache(tmp_path):
    assert isinstance(
        make_prompt_cache(Settings(prompt_cache_backend="memory")), InMemoryPromptCache
    )
    s = make_prompt_cache(
        Settings(prompt_cache_backend="local", prompt_cache_path=str(tmp_path / "p.json"))
    )
    assert isinstance(s, LocalFilePromptCache)
    with pytest.raises(ValueError):
        make_prompt_cache(Settings(prompt_cache_backend="dynamodb"))


def test_make_llm_dispatch():
    assert isinstance(make_llm(Settings(llm_provider="mock")), MockLLM)
    be = make_llm(Settings(llm_provider="bedrock", llm_model="us.anthropic.claude-sonnet-4-6"))
    assert be.model_id == "us.anthropic.claude-sonnet-4-6"


def test_dynamodb_prompt_cache_moto():
    pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    from moto import mock_aws

    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="pc",
            KeySchema=[{"AttributeName": "key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        c = DynamoDBPromptCache("pc", client=ddb)
        assert c.get("k") is None
        c.put("k", "value")
        assert c.get("k") == "value"
