# Runbook — /search service

## Symptom: /search is slow or timing out under load

1. **Check the pool, not the query first.**
   ```bash
   curl -s localhost:8000/healthz | jq
   # {"status":"ok","pool_size":20,"pool_free":0}
   ```
   `pool_free: 0` sustained → every connection is checked out. Either
   traffic exceeds capacity or queries are slow and holding
   connections. Go to step 2.

2. **Is the index being used?** Run the EXPLAIN from
   `docs/explain-output.md`. If you see `Seq Scan`, the index is
   missing or unused:
   ```bash
   psql "$DATABASE_URL" -c "\d+ specimens"     # is specimens_species_trgm_idx listed?
   psql "$DATABASE_URL" -f migrations/0001_search_index.sql   # idempotent re-apply
   psql "$DATABASE_URL" -c "ANALYZE specimens;"
   ```

3. **Are queries hitting `command_timeout`?** Look for
   `event=search_db_error` in the logs. A 5 s timeout cancellation
   means the query itself is pathological (e.g. a <3-char pattern
   forcing a seq scan on a huge table). Require ≥3-char queries at the
   API edge.

4. **Connection exhaustion at Postgres.**
   ```bash
   psql "$DATABASE_URL" -c \
     "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"
   ```
   If `active + idle` approaches `max_connections`, the per-process
   pools (workers × max_size) are over-provisioned. Either lower
   `POOL_MAX_SIZE` or put PgBouncer (transaction mode) in front.

## Symptom: every request returns 503

The pool never came up. Check startup logs for `event=startup_begin`
without a following `event=startup_complete`, and for the
`RuntimeError: DATABASE_URL is not set` that `db.create_pool` raises
loudly at startup (by design — the broken baseline leaked a bad DSN
into every request instead).

## Symptom: /healthz returns 500 (LB rotates the instance)

The pool exists but `SELECT 1` failed — Postgres is unreachable or
out of connections. Check Postgres health directly; the app is
correctly reporting downstream failure, not causing it.

## Tuning knobs (all env vars)

| Var | Default | When to change |
|---|---|---|
| `POOL_MIN_SIZE` | 5 | Raise if cold-start latency on first burst matters |
| `POOL_MAX_SIZE` | 20 | Lower if `workers × max_size` nears `max_connections` |
| `COMMAND_TIMEOUT_SEC` | 5.0 | Lower to fail fast; raise only for known-heavy queries |
| `LOG_LEVEL` | INFO | DEBUG to see per-request latency_ms during an incident |

## Re-running the load test (before/after)

```bash
# 1. seed
psql "$DATABASE_URL" -f migrations/0000_schema.sql
python scripts/seed_pg.py --rows 500000

# 2. BEFORE — start the broken baseline, then load it
DATABASE_URL=$DATABASE_URL uvicorn broken.app:app --port 8000 &
locust -f loadtest/locustfile.py --host=http://localhost:8000 \
       --users 50 --spawn-rate 10 --run-time 30s --headless

# 3. apply the index + AFTER — start the fixed app, same load
psql "$DATABASE_URL" -f migrations/0001_search_index.sql
DATABASE_URL=$DATABASE_URL uvicorn app:app --port 8000 &
locust -f loadtest/locustfile.py --host=http://localhost:8000 \
       --users 50 --spawn-rate 10 --run-time 30s --headless
```

Capture the median / p95 / failure-count from each run for the PR.
