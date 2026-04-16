# Architecture: LLM PowerPoint Editing Agent

## Before (Broken) — Sequential, ~20s for 10 slides

```
User request: "Make these 10 slides more concise"
    │
    ▼
┌────────────────────────────────────────────────────────────┐
│ for each slide (sequential):                               │
│                                                            │
│   Slide 1 ──► Claude Sonnet ──► ~2s ──► result 1          │
│   Slide 2 ──► Claude Sonnet ──► ~2s ──► result 2          │
│   Slide 3 ──► Claude Sonnet ──► ~2s ──► result 3          │
│   ...                                                      │
│   Slide 10 ──► Claude Sonnet ──► ~2s ──► result 10        │
│                                                            │
│   Total: ~20s  (10 × 2s, one after another)               │
│                                                            │
│   System prompt (1500 tokens) re-sent in full each call   │
│   = 15,000 input tokens billed at full price              │
│   No fallback: if Sonnet is slow, everything is slow      │
│   No timeout: one hung call → entire request hangs        │
└────────────────────────────────────────────────────────────┘
    │
    ▼
Response after ~20s  ❌ too slow
```

---

## After (Fixed) — Parallel, ~2–3s for 10 slides

```
User request: "Make these 10 slides more concise"
    │
    ▼
┌────────────────────────────────────────────────────────────┐
│ asyncio.gather (all slides in parallel):                   │
│                                                            │
│   Slide 1 ──►┐                                            │
│   Slide 2 ──►│                                            │
│   Slide 3 ──►├──► Claude Sonnet (cached system prompt)    │
│   Slide 4 ──►│     max 5 concurrent (semaphore)           │
│   Slide 5 ──►┘     timeout: 5s per slide                  │
│              ─── wait ───                                  │
│   Slide 6 ──►┐                                            │
│   Slide 7 ──►│                                            │
│   Slide 8 ──►├──► Claude Sonnet (cached system prompt)    │
│   Slide 9 ──►│                                            │
│   Slide 10──►┘                                            │
│                                                            │
│   If any slide times out:                                  │
│     └──► Claude Haiku (fallback, ~500ms)                  │
│                                                            │
│   Total: ~2–3s  (two rounds of 5 parallel calls)          │
│                                                            │
│   System prompt cached after first call:                   │
│     Call 1: 1500 tokens × 1.25 = cache write              │
│     Calls 2–10: 1500 tokens × 0.10 = cache read           │
└────────────────────────────────────────────────────────────┘
    │
    ▼
Response after ~2–3s  ✅ within target
```

---

## Three Optimisations Explained

### 1. Parallel Calls (asyncio.gather)

```python
# BEFORE — sequential
for slide in slides:
    result = client.messages.create(...)    # blocks 2s
    results.append(result)
# Total: 10 × 2s = 20s

# AFTER — parallel
tasks = [_call_model(slide) for slide in slides]
results = await asyncio.gather(*tasks)     # all run at once
# Total: max(2s, 2s, 2s, ...) ≈ 2s (limited by the slowest call)
```

**Why asyncio and not threads?**
- API calls are I/O-bound (waiting for network), not CPU-bound.
- asyncio handles I/O-bound work with a single thread — no GIL contention,
  no thread overhead, no race conditions.
- The Anthropic SDK provides `AsyncAnthropic` which is designed for this.

### 2. Prompt Caching (cache_control)

```python
# BEFORE — plain string, no caching
system=SYSTEM_PROMPT    # 1500 tokens billed at full rate, every call

# AFTER — block with cache_control
system=[{
    "type": "text",
    "text": SYSTEM_PROMPT,
    "cache_control": {"type": "ephemeral"}    # tells Anthropic to cache this
}]
```

**How the cache works internally:**

```
Call 1: Anthropic API receives system prompt (1500 tokens)
        → No cache entry exists → CACHE WRITE
        → Billed: 1500 × $3/1M × 1.25 = $0.005625
        → Response includes: cache_creation_input_tokens: 1500

Call 2 (within 5 minutes): Same system prompt hash
        → Cache entry found → CACHE READ
        → Billed: 1500 × $3/1M × 0.10 = $0.00045
        → Response includes: cache_read_input_tokens: 1500
        → TTL reset to 5 minutes from now

Calls 3–10: Same as Call 2 — cache read at 10% cost
```

**Cost comparison for 10-slide request:**

| Metric | Without caching | With caching |
|--------|----------------|--------------|
| System prompt tokens billed | 15,000 (10 × 1500) | 1,875 + 13,500×0.10 = 3,225 |
| Effective cost multiplier | 1.0× | ~0.22× |
| Saving | — | ~78% on system prompt tokens |

**Constraints:**
- Minimum cacheable prefix: 1024 tokens (Sonnet/Haiku)
- TTL: 5 minutes (ephemeral). Each hit resets the timer.
- Cache hierarchy: tools → system → messages. Changing any level
  invalidates that level and everything after it.
- Cache is per-model: Sonnet cache ≠ Haiku cache.

### 3. Fallback Model

```python
try:
    # Primary: Sonnet — highest quality, ~2s latency
    result = await asyncio.wait_for(
        _call_model(PRIMARY_MODEL, slide, instruction),
        timeout=SLIDE_TIMEOUT,
    )
except (asyncio.TimeoutError, anthropic.APIStatusError):
    # Fallback: Haiku — lower quality, ~500ms latency, cheaper
    result = await _call_model(FALLBACK_MODEL, slide, instruction)
```

**When fallback triggers:**
- `asyncio.TimeoutError` — primary took longer than SLIDE_TIMEOUT (5s)
- `anthropic.APIStatusError` — covers 429 (rate limited), 500 (server error),
  503 (overloaded), 529 (API overloaded)

**When fallback does NOT trigger:**
- `anthropic.AuthenticationError` — wrong API key. Fallback won't help.
- `anthropic.APIConnectionError` — network down. Caught by the outer
  except in `_edit_slide_with_fallback` and returned as an error result.

---

## Rate Limits and Concurrency

Anthropic enforces per-minute rate limits by tier:

| Tier | RPM (requests/min) | TPM (tokens/min) |
|------|-------------------|-------------------|
| Tier 1 (new accounts) | 60 | 60,000 |
| Tier 2 | 1,000 | 120,000 |
| Tier 3 | 2,000 | 300,000 |
| Tier 4 | 4,000 | 1,000,000 |

Our `MAX_CONCURRENT=5` semaphore ensures we never have more than 5
in-flight API calls at once. For a Tier 1 account (60 RPM), this is
safe: 5 concurrent calls completing in ~2s each = ~150 calls/min at peak.

If we removed the semaphore and sent 50 slides at once:
```
50 simultaneous requests → 429 Too Many Requests
→ All slides fail → all fall back to Haiku → Haiku also rate-limited
→ Cascade failure
```

---

## Pricing Comparison

**Sonnet 4.6:** $3/1M input, $15/1M output
**Haiku 4.5:** $1/1M input, $5/1M output

| Scenario | Model | Input tokens | Output tokens | Cost |
|----------|-------|-------------|---------------|------|
| 10 slides, no caching | Sonnet | 15,000 | 5,000 | $0.12 |
| 10 slides, with caching | Sonnet | ~3,225 effective | 5,000 | $0.085 |
| 10 slides, all fallback | Haiku | ~3,225 effective | 5,000 | $0.028 |

---

## Config Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | (required) | API key — read by SDK automatically |
| `PRIMARY_MODEL` | `claude-sonnet-4-6-20250929` | First-choice model |
| `FALLBACK_MODEL` | `claude-haiku-4-5-20251001` | Used on timeout/error |
| `SLIDE_TIMEOUT` | `5.0` | Seconds before falling back |
| `MAX_CONCURRENT` | `5` | Max parallel API calls |
| `LOG_LEVEL` | `INFO` | Python logging level |
