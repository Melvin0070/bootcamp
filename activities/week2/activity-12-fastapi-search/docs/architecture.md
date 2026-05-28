# Activity 12 — Architecture

## Before / after

```
Before (broken/app.py):

  client ──▶ FastAPI worker (1 event loop)
                 │
                 │  def search():              ← SYNC handler
                 │    psycopg2.connect(...)     ← new TCP+TLS+auth / request
                 │    cur.execute(f"... '%{q}%'")  ← SQL injection + seq scan
                 │    cur.fetchall()            ← BLOCKS the event loop
                 ▼
              Postgres (no index on species)
                 │
                 └─ full table scan, ~400 ms each
                    + max_connections=100 exhausted under load
                    → every other request on the worker waits in line

After (app.py + db.py):

  client ──▶ FastAPI worker (1 event loop)
                 │
                 │  async def search():         ← ASYNC handler
                 │    await pool.acquire()       ← checkout from warm pool
                 │    await conn.fetch("... ILIKE $1", "%q%")  ← awaits, frees loop
                 ▼
              asyncpg pool (min 5 / max 20, reused)
                 │
                 ▼
              Postgres (pg_trgm GIN index on species)
                 │
                 └─ Bitmap Index Scan, sub-ms
                    + bounded connections, no exhaustion
                    → loop free to serve other requests while PG works
```

## The three root causes (and the three fixes)

| Root cause | Symptom under load | Fix |
|---|---|---|
| New connection per request (`psycopg2.connect`) | TCP+TLS+auth handshake on every request; Postgres `max_connections` exhausted in ~2 s at 100 RPS | One `asyncpg` pool (`min_size=5, max_size=20`) created at lifespan, shared across requests |
| Synchronous DB calls in an async-capable server | `cursor.execute()` blocks the event loop; effective concurrency = 1 per worker | `await conn.fetch(...)` releases the loop while Postgres works; one worker serves many concurrent requests |
| No index on the search column | `ILIKE '%q%'` = sequential scan, ~400 ms on 500 k rows | `pg_trgm` GIN index → `Bitmap Index Scan`, sub-millisecond |

## Why asyncpg (not SQLAlchemy async / aiopg)

- **Fastest** of the three — asyncpg speaks the Postgres binary
  protocol directly, no psycopg layer.
- **Native bounded pool** with health-checking and
  `max_inactive_connection_lifetime`.
- **Async-only API** — there's no sync method to call by accident
  from a coroutine, which is the exact mistake the broken baseline
  makes.

The trade-off: asyncpg is Postgres-only (no MySQL/SQLite), and its
parameter style is `$1` not `%s`. For a Postgres-backed service
that's all upside.

## Why pg_trgm + ILIKE (not a btree, not the `%` similarity operator)

A plain btree on `species` is **left-anchored** — it can serve
`species LIKE 'stego%'` but not `species ILIKE '%stego%'`, which is
what a search box needs. A leading wildcard forces a sequential scan.

`pg_trgm`'s GIN index indexes the 3-grams of every value, so it
accelerates **arbitrary-substring** `LIKE` / `ILIKE`. That's the
documented use case for the extension.

We use `ILIKE '%q%'` (with the wildcards in the **bound parameter**,
not the SQL text), not the pg_trgm `%` similarity operator, because:
- `%` depends on `pg_trgm.similarity_threshold` (default 0.3). A short
  query like `stego` against a long value like `stegosaurus
  specimen 12345` can score below the threshold and return nothing —
  a surprising "no results" for a clearly-matching substring.
- `ILIKE` is exact substring containment, which is what users expect
  from a search box, and it still uses the GIN index.

Caveat documented in the migration: patterns shorter than 3 chars
can't form a trigram and fall back to a sequential scan; the API caps
query length and the UI should require ≥3 chars for indexed search.

## Connection-pool sizing

```
min_size = 5      warm baseline; first burst doesn't pay full handshake
max_size = 20     headroom: 4 Uvicorn workers × 20 = 80 < PG max_connections (100)
command_timeout = 5s   caps the blast radius of one slow query
```

If you scale to more workers, the invariant to preserve is
`workers × max_size < postgres.max_connections − headroom_for_admin`.
Past that point you need a server-side pooler (PgBouncer in
transaction mode) rather than a bigger per-process pool.

## Injection safety

The broken baseline interpolates the query with an f-string:
`f"... ILIKE '%{q}%'"` — a textbook injection. The fix binds the
parameter: `conn.fetch("... ILIKE $1", "%" + q + "%")`. The `%`
wildcards are part of the **value**, and `q` is sent over the wire as
a bound literal — a payload like `'; DROP TABLE specimens; --` is
matched as a string and matches nothing. There's an integration test
that fires exactly that payload and asserts the table survives.

## Edge cases handled

- **Empty / missing query** → 422 at the validation layer (`min_length=1`),
  before any DB work — an empty query would otherwise scan the table.
- **`limit` out of range** → 422 (`ge=1, le=100`); caps the worst-case
  result set.
- **Pool not ready (startup race)** → 503, not a 500 crash; the LB
  knows to wait, not to rotate the instance.
- **Pool unhealthy** → `/healthz` returns 500 so the LB rotates the
  instance; distinct from the 503 "still warming up" case.
- **Slow query** → `command_timeout=5s` cancels it; one bad request
  can't hold a connection forever.
- **Sub-3-char query** → still correct (returns results) but falls
  back to a seq scan; documented, and the latency is acceptable
  because the result set is large anyway.
- **SQL injection payload** → bound as data, matched as a literal.

## What's NOT in this activity

- Server-side pooling (PgBouncer) — only needed past the
  per-process-pool ceiling; documented as the next step.
- Full-text search (`tsvector`/`tsquery`) — better for natural-language
  search but heavier; substring search is what the broken endpoint
  did, so the fix preserves semantics.
- Caching / read replicas — premature before the index + pool fix,
  which is where the actual bottleneck was.
