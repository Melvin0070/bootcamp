# Activity 2: Optimise a Slow LLM PowerPoint Editing Agent

**Week:** 1 | **Day:** 2 | **Course alignment:** Agentic AI

## Problem Statement

An LLM-based PowerPoint editing agent takes **>10 seconds per request** — unusable in production.

Root causes:
- All LLM calls are made **sequentially** even when independent
- No **prompt caching** — same prompts re-sent on every call
- No **fallback model** when primary is slow or unavailable

## What to Fix

- [ ] Parallelise independent LLM calls (e.g., `asyncio.gather` or thread pool)
- [ ] Implement **prompt caching** (cache stable system prompts)
- [ ] Add a **fallback model** (e.g., switch to a cheaper/faster model on timeout)
- [ ] Target: reduce latency to **under 3 seconds**

## Acceptance Criteria

- End-to-end request completes in <3s for a typical slide deck
- Cached prompts are reused across calls
- Fallback triggers correctly when primary model is unavailable

## PR Checklist

- [ ] Fix applied in `broken/` → working code committed
- [ ] Clear PR description (what was broken, what you changed, why)
- [ ] 2–5 min video walkthrough (before/after)

## Notes

_Add your findings, decisions, and observations here as you work._
