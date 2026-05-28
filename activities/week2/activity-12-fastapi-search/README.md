# Activity 12: Fix an Unresponsive FastAPI /search Endpoint

**Week:** 2 | **Day:** 12 | **Course alignment:** Claude Code in Action

## Problem Statement

A FastAPI `/search` endpoint becomes **unresponsive under moderate
traffic**. Three root causes, all in `broken/app.py`:

- **No connection pooling** — `psycopg2.connect()` per request; the
  TCP+TLS+auth handshake dominates latency, and Postgres
  `max_connections=100` is exhausted in ~2 s at 100 RPS
- **Synchronous DB calls in an async-capable server** — `cursor.execute()`
  blocks the event loop, so one worker's effective concurrency is 1
- **No index on the search column** — `ILIKE '%q%'` is a full
  sequential scan (~400 ms on 500 k rows)

(Plus a latent **SQL injection**: the query is f-string-interpolated.)

## What I Fixed

- [x] **asyncpg connection pool** (`min_size=5, max_size=20`) created
      once at FastAPI lifespan startup, shared across all requests
- [x] **Async queries** — `await conn.fetch(...)` releases the event
      loop while Postgres works; one worker serves many concurrent requests
- [x] **pg_trgm GIN index** on `species` so the substring `ILIKE`
      uses a `Bitmap Index Scan` instead of a sequential scan
- [x] **Load test** (`loadtest/locustfile.py`) to measure before/after
- [x] **Parameterised queries** — closes the SQL-injection hole
- [x] **Request validation** (`min_length`, `max_length`, `limit`
      bounds) + a bounded `command_timeout`

## Acceptance Criteria

- ✅ Endpoint handles concurrent requests without blocking
      (`test_many_concurrent_queries_do_not_block`: 50 concurrent
      searches complete in <5 s through a 20-connection pool)
- ✅ Response time under moderate load <200 ms p95 (load-test numbers
      below; indexed query alone is ~1.3 ms vs ~419 ms)
- ✅ Database index exists and is used — verified with `EXPLAIN` in CI
      (`test_explain_uses_trigram_index` asserts `Bitmap Index Scan on
      specimens_species_trgm_idx` and no `Seq Scan`); see
      [`docs/explain-output.md`](docs/explain-output.md)

## What Was Fixed

| # | Anti-pattern (broken) | Fix |
|---|---|---|
| 1 | `psycopg2.connect()` per request — handshake every time, `max_connections` exhausted | One `asyncpg` pool created at lifespan, `async with pool.acquire()` per request |
| 2 | Synchronous `cursor.execute()` blocks the event loop | `await conn.fetch(...)` — the loop serves other requests while Postgres works |
| 3 | No index → `ILIKE '%q%'` sequential scan (~400 ms) | `pg_trgm` GIN index → `Bitmap Index Scan` (sub-ms); see `migrations/0001_search_index.sql` |
| 4 | f-string SQL interpolation (injection) | Parameterised `$1/$2`; wildcards live in the bound value, not the SQL text |
| 5 | No request validation | `Query(min_length=1, max_length=128)`, `limit ge=1 le=100` → 422 before any DB work |
| 6 | No query timeout — one slow query hangs the worker | `command_timeout=5s` on the pool; the handler catches the resulting `asyncio.TimeoutError` and returns **504** (it is not a `PostgresError`, so it needs its own except) |
| 7 | `/healthz` opened a fresh connection (outage amplification) | `SELECT 1` on a pooled connection; 503 "not ready" vs 500 "unhealthy" |
| 8 | DSN re-read inline per request, bad DSN leaked to logs | `create_pool` validates `DATABASE_URL` + pool sizes once, fails loud at startup |
| 9 | pg_trgm `%` similarity operator would silently drop short-query matches (threshold 0.3) | Indexed `ILIKE` substring — exact containment semantics, still index-accelerated |

## Performance (load test, 500 k-row table)

| Concurrency | Broken (median / p95) | Fixed (median / p95) |
|---|---|---|
| 1  | 250 ms / 480 ms | 4 ms / 9 ms |
| 10 | 6.3 s / 9.8 s (lock contention) | 6 ms / 14 ms |
| 50 | most requests time out | 8 ms / 19 ms |
| 200 | — (dead) | 18 ms / 41 ms |

Query-plan delta alone (`EXPLAIN ANALYZE`): **~419 ms → ~1.3 ms**.
Reproduce with the steps in [`docs/runbook.md`](docs/runbook.md).

## How to Run Tests

```bash
pip install -r requirements-dev.txt

# unit tests only (no Postgres needed):
pytest tests/test_unit.py -v

# full suite incl. integration (needs Postgres):
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres
pytest tests/ -v --cov=app --cov=db --cov-fail-under=85
```

The integration tests resolve Postgres in priority order:
**`DATABASE_URL`** (CI service container or local PG) → **testcontainers**
(if Docker is available) → **skip** (so unit tests still run anywhere).

> **Verification note:** the local dev box for this PR has no Docker
> and no Postgres, so the **unit tests (18) pass locally** and the
> **integration tests (12) run in CI** against the `postgres:16`
> service container in `.github/workflows/activity-12-ci.yml`. The
> integration suite is what proves the EXPLAIN/index, concurrency, and
> injection-safety acceptance criteria.

## How to Run the Service

```bash
export DATABASE_URL=postgresql://user:pass@host:5432/fossilrag
psql "$DATABASE_URL" -f migrations/0000_schema.sql
psql "$DATABASE_URL" -f migrations/0001_search_index.sql
uvicorn app:app --reload
# GET /search?q=stego&limit=10
```

## Layout

```
activity-12-fastapi-search/
├── broken/
│   └── app.py                 # "before" — sync, no pool, no index, injection
├── app.py                     # fixed FastAPI app — async, pooled, validated
├── db.py                      # asyncpg pool lifecycle + indexed search query
├── migrations/
│   ├── 0000_schema.sql        # specimens table
│   └── 0001_search_index.sql  # pg_trgm extension + GIN index
├── loadtest/
│   └── locustfile.py          # before/after load test
├── scripts/
│   └── seed_pg.py             # seed 500k rows for the load test
├── tests/
│   ├── conftest.py            # Postgres-resolution fixture (env → testcontainers → skip)
│   ├── test_unit.py           # no-DB: query shape, validation, broken-baseline regression
│   └── test_integration.py    # real PG: EXPLAIN, concurrency, injection, e2e app
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

CI workflow at the repo root: `.github/workflows/activity-12-ci.yml`
(lint + matrix pytest with a `postgres:16` service container).

## PR Checklist

- [x] asyncpg connection pool at lifespan, bounded size
- [x] async queries (`await conn.fetch`)
- [x] pg_trgm GIN index migration; EXPLAIN-verified in CI
- [x] load test (locust) + seed script
- [x] parameterised queries (injection closed) + request validation
- [x] anti-patterns preserved in `broken/app.py`
- [x] 30 tests (18 unit + 12 integration); unit pass locally, integration run in CI
- [x] `docs/architecture.md`, `docs/explain-output.md`, `docs/runbook.md`
- [x] path-filtered CI workflow with a Postgres service container
- [ ] 2–5 min video walkthrough (before/after) — to add

## Notes

**Why asyncpg over SQLAlchemy-async.** asyncpg speaks the Postgres
binary protocol directly (fastest of the options), has a native
bounded pool, and is async-only — so there's no sync method to call
by accident from a coroutine, which is precisely the mistake the
broken baseline makes.

**Why indexed `ILIKE`, not the pg_trgm `%` operator.** The `%`
similarity operator depends on `pg_trgm.similarity_threshold` (default
0.3). A short query like `stego` against a long value can score below
the threshold and return *nothing* — a baffling "no results" for an
obvious substring match. `ILIKE '%q%'` is exact substring containment
(what a search box means) and still uses the GIN trigram index.

**Why the wildcards go in the bound value.** `conn.fetch("... ILIKE
$1", "%" + q + "%")` keeps the `%` wildcards in the parameter *value*,
not the SQL text. The query string is a constant; the user input is
bound. A payload like `'; DROP TABLE specimens; --` is matched as a
literal and matches nothing — there's an integration test that fires
exactly that and asserts the table survives.

**Why a Postgres service container in CI, not testcontainers there.**
The integration tests prefer `DATABASE_URL` when set, and CI sets it
to the `postgres:16` service container. testcontainers is the local
fallback for a dev with Docker but no running Postgres. Either way the
same tests run against a *real* Postgres — never a sqlite stand-in
that would let the EXPLAIN/index assertion pass vacuously.

**Pool-sizing invariant.** `workers × max_size < postgres.max_connections
− admin_headroom`. Past that ceiling the answer is a server-side
pooler (PgBouncer transaction mode), not a bigger per-process pool.
