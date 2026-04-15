# Activity 7: Fix a Slow Vector Search System

**Week:** 2 | **Day:** 7 | **Course alignment:** AWS Technical Essentials

## Problem Statement

A vector search system **rebuilds the FAISS/Chroma index on every request**, causing >500ms latency. The index should be loaded once and kept warm.

## What to Fix

- [ ] Load the index **once at application startup**, not per request
- [ ] Keep the loaded index **in memory** (module-level or singleton)
- [ ] Add a **refresh mechanism** (e.g., reload index on a schedule or via a signal)
- [ ] Target: **sub-100ms** search latency after warm-up

## Acceptance Criteria

- First request loads the index; subsequent requests reuse it
- Search latency is <100ms for typical queries
- Index can be refreshed without restarting the service

## PR Checklist

- [ ] Fix applied in `broken/` → working code committed
- [ ] Clear PR description (what was broken, what you changed, why)
- [ ] 2–5 min video walkthrough (before/after)

## Notes

_Add your findings, decisions, and observations here as you work._
