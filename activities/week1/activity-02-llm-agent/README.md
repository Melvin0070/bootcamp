# Activity 2: Optimise a Slow LLM PowerPoint Editing Agent

**Week:** 1 | **Day:** 2 | **Course alignment:** Agentic AI

---

## Problem Statement

An LLM-based PowerPoint editing agent takes **>10 seconds per request**:

| Root cause | Impact |
|------------|--------|
| Sequential LLM calls | 10 slides × ~2s each = ~20s total |
| No prompt caching | 1500-token system prompt re-sent at full price on every call |
| No fallback model | If Sonnet is slow/rate-limited, entire request fails |
| No timeout handling | One hung API call blocks the request indefinitely |
| Hardcoded API key | Security risk if repo is public |
| No concurrency control | Burst traffic exhausts API rate limits |

---

## What Was Fixed

- [x] **Parallel calls** via `asyncio.gather` — all slides processed concurrently (~2–3s total)
- [x] **Prompt caching** — `cache_control: {"type": "ephemeral"}` on system prompt (~78% cost saving)
- [x] **Fallback model** — Haiku handles requests when Sonnet times out or errors
- [x] **Timeout** per slide (configurable, default 5s)
- [x] **Semaphore** caps concurrent API calls (default 5) to respect rate limits
- [x] **Environment variables** for all config (API key, models, timeout, concurrency)
- [x] **Structured JSON logging** with cache hit/miss and fallback tracking
- [x] **Graceful degradation** — both models failing returns an error result, doesn't crash

---

## Repo Layout

```
activity-02-llm-agent/
├── broken/
│   ├── agent.py            ← sequential, no caching, no fallback
│   └── requirements.txt
├── agent.py                ← fixed: parallel, cached, fallback
├── requirements.txt
├── requirements-dev.txt
├── .env.example
├── tests/
│   └── test_agent.py       ← 12 tests (parallel, caching, fallback, concurrency)
└── docs/
    └── architecture.md      ← diagrams, cost comparison, config reference
```

---

## Running Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v --cov=agent --cov-report=term-missing
```

---

## Running the Agent

```bash
cp .env.example .env
# Edit .env with your real ANTHROPIC_API_KEY

python agent.py
```

---

## PR Rubric Self-Check

| Criterion | Evidence |
|-----------|----------|
| **Code Correctness** | Parallel execution, cache_control on system prompt, fallback on timeout + API error, semaphore for rate limits |
| **Problem Solving** | asyncio.gather (not threads — I/O bound), prompt caching cost analysis, fallback hierarchy (timeout → Haiku → error result) |
| **Code Quality** | No hardcoded values, structured JSON logging, async/await throughout, type hints |
| **PR Description** | Before/after diagrams, cost table, rate limit table, config reference |
| **Completeness** | 12 tests, architecture doc, .env.example, both broken and fixed code |
