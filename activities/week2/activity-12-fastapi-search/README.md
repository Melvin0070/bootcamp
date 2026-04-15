# Activity 12: Fix an Unresponsive FastAPI /search Endpoint

**Week:** 2 | **Day:** 12 | **Course alignment:** Claude Code in Action

## Problem Statement

A FastAPI `/search` endpoint becomes **unresponsive under moderate traffic** due to:
- No **connection pooling** — a new DB connection is opened per request
- **Synchronous** database calls blocking the event loop
- No **database index** on the queried column

## What to Fix

- [ ] Add a **connection pool** (e.g., `asyncpg` pool or SQLAlchemy async pool)
- [ ] Convert database calls to **async** (`await db.fetch(...)`)
- [ ] Create the required **database index** on the search column
- [ ] Add a basic **load test** (e.g., `locust` or `pytest-benchmark`) to verify improvement

## Acceptance Criteria

- Endpoint handles concurrent requests without blocking or timing out
- Response time under moderate load is <200ms p95
- Database index exists and is used (verify with EXPLAIN)

## PR Checklist

- [ ] Fix applied in `broken/` → working code committed
- [ ] Clear PR description (what was broken, what you changed, why)
- [ ] 2–5 min video walkthrough (before/after)

## Notes

_Add your findings, decisions, and observations here as you work._
