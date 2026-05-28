"""Mock summariser backing ``/mutate`` (PR0 fidelity).

The real ``/mutate`` retrieves relevant fossils, builds a prompt, and calls an
LLM (AWS Bedrock via the Converse API) with **Prompt Fossilization** (prompt
caching) behind a pluggable provider interface — that lands in PR4. PR0 ships a
deterministic, dependency-free *extractive* placeholder so the API surface is
complete and the spine is demonstrably end-to-end on both endpoints, at $0 and
with no keys. ``MutateResponse.mock`` is True until PR4 swaps in the real LLM.
"""

from __future__ import annotations

from fossilrag.models import ExcavateHit

MOCK_NOTE = (
    "Mock extractive summary. The real LLM path (AWS Bedrock Converse API + "
    "Prompt Fossilization) lands in PR4; this placeholder grounds the response "
    "in the retrieved fossils so the /mutate surface is callable today."
)


def mock_summarise(query: str, instruction: str | None, hits: list[ExcavateHit]) -> str:
    """Deterministic extractive 'summary' of the retrieved fossils.

    Not semantic — it simply leads with the top fragments in rank order. Its
    job is to prove the retrieve→summarise wiring, not to be a good summary.
    """
    if not hits:
        return "No fossils matched the query; there is nothing to summarise."

    lead = f"Based on {len(hits)} retrieved fossil fragment(s)"
    if instruction:
        lead += f" (instruction: {instruction!r})"
    bullets = []
    for h in hits:
        first = h.content.strip().split(". ", 1)[0].strip()
        if len(first) > 200:
            first = first[:200].rstrip() + "…"
        bullets.append(f"- {first}")
    return lead + ":\n" + "\n".join(bullets)
