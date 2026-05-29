"""OpenAI-compatible LLM + embedder (injected fakes — no SDK, no network, $0)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from fossilrag.embedding.openai_compat import OpenAICompatEmbedder
from fossilrag.llm.openai_compat import OpenAILLM
from fossilrag.models import ChatMessage, ExcavateHit


def _hit(content: str) -> ExcavateHit:
    return ExcavateHit(
        chunk_id="c1",
        doc_id="d1",
        source_id="s",
        ordinal=0,
        content=content,
        score=0.9,
        layer_version=1,
        geological_age="Late Cretaceous",
    )


class _FakeChatClient:
    """Mimics openai client.chat.completions.create(...)."""

    def __init__(self, reply: str) -> None:
        self.calls: list[dict] = []
        completions = SimpleNamespace(create=self._create)
        self.chat = SimpleNamespace(completions=completions)
        self._reply = reply

    def _create(self, **kwargs):  # noqa: ANN003, ANN202
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._reply))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
        )


def test_openai_llm_summarise_builds_grounded_messages():
    client = _FakeChatClient("Velociraptor hunted with a sickle claw.")
    llm = OpenAILLM(model_id="gemini-2.0-flash", client=client)
    res = llm.summarise(query="how did it hunt?", instruction=None, hits=[_hit("sickle claw")])

    assert res.text == "Velociraptor hunted with a sickle claw."
    assert res.model_id == "gemini-2.0-flash"
    assert res.output_tokens == 7
    sent = client.calls[0]
    assert sent["model"] == "gemini-2.0-flash"
    assert sent["messages"][0]["role"] == "system"
    assert "sickle claw" in sent["messages"][-1]["content"]  # the fossil grounded the prompt
    # Portable request: no max_tokens / temperature (current reasoning models
    # reject them; Gemini wants max_tokens) — only model + messages are sent.
    assert "max_tokens" not in sent and "temperature" not in sent


def test_openai_llm_chat_and_edit():
    client = _FakeChatClient("ok")
    llm = OpenAILLM(model_id="gpt-4o-mini", client=client)
    assert llm.chat(messages=[ChatMessage(role="user", content="hi")], hits=[]).text == "ok"
    assert llm.edit(text="old", instruction="punchier").text == "ok"


def test_openai_llm_tolerates_empty_choices():
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: SimpleNamespace(choices=[], usage=None))
        )
    )
    res = OpenAILLM(model_id="m", client=client).summarise(query="q", instruction=None, hits=[])
    assert res.text == ""


class _FakeEmbedClient:
    def __init__(self, vector: list[float]) -> None:
        self._vec = vector
        self.embeddings = SimpleNamespace(create=self._create)
        self.calls: list[dict] = []

    def _create(self, **kwargs):  # noqa: ANN003, ANN202
        self.calls.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(embedding=self._vec) for _ in kwargs["input"]])


def test_openai_embedder_l2_normalises():
    client = _FakeEmbedClient([0.0, 3.0, 4.0])  # |v| = 5
    emb = OpenAICompatEmbedder(model_id="text-embedding-004", dimensions=3, client=client)
    vecs = emb.encode(["a", "b"])
    assert vecs.shape == (2, 3)
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0)  # unit-normalised
    assert np.allclose(vecs[0], [0.0, 0.6, 0.8])
    assert client.calls[0]["model"] == "text-embedding-004"


def test_openai_embedder_empty_and_dim_mismatch():
    client = _FakeEmbedClient([0.1, 0.2, 0.3])
    emb = OpenAICompatEmbedder(model_id="m", dimensions=3, client=client)
    assert emb.encode([]).shape == (0, 3)
    bad = OpenAICompatEmbedder(model_id="m", dimensions=4, client=client)  # model gives 3
    with pytest.raises(ValueError, match="expected 4"):
        bad.encode(["x"])
