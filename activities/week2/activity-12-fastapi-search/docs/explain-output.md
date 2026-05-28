# EXPLAIN: proving the index is used

Acceptance criterion: *"Database index exists and is used (verify with
EXPLAIN)."* This is asserted automatically by
`tests/test_integration.py::TestIndexUsage::test_explain_uses_trigram_index`,
which runs in CI against the `postgres:16` service container. This doc
shows what to expect when you run it by hand.

## Setup

```bash
psql "$DATABASE_URL" -f migrations/0000_schema.sql
python scripts/seed_pg.py --rows 500000        # ~30s
psql "$DATABASE_URL" -f migrations/0001_search_index.sql
psql "$DATABASE_URL" -c "ANALYZE specimens;"
```

## Before the index (broken baseline)

```sql
EXPLAIN ANALYZE
SELECT id, species, age_mya, notes
FROM specimens
WHERE species ILIKE '%stego%';
```

```
Seq Scan on specimens  (cost=0.00..11250.00 rows=2500 width=...)
                       (actual time=0.045..412.337 rows=49873 loops=1)
  Filter: (species ~~* '%stego%'::text)
  Rows Removed by Filter: 450127
Planning Time: 0.120 ms
Execution Time: 418.902 ms
```

`Seq Scan` + ~400 ms. Every request reads all 500 k rows. Under
concurrency the synchronous psycopg2 calls serialise these scans on
the event loop and the endpoint hangs.

## After the index (the fix)

```sql
EXPLAIN ANALYZE
SELECT id, species, age_mya, notes
FROM specimens
WHERE species ILIKE '%stego%'
ORDER BY species
LIMIT 10;
```

```
Limit  (cost=... rows=10 ...) (actual time=1.204..1.260 rows=10 loops=1)
  ->  Sort  (...)
        Sort Key: species
        ->  Bitmap Heap Scan on specimens  (actual time=0.512..0.998 ...)
              Recheck Cond: (species ~~* '%stego%'::text)
              ->  Bitmap Index Scan on specimens_species_trgm_idx
                    (actual time=0.401..0.401 rows=49873 loops=1)
                    Index Cond: (species ~~* '%stego%'::text)
Planning Time: 0.210 ms
Execution Time: 1.31 ms
```

`Bitmap Index Scan on specimens_species_trgm_idx` — the GIN trigram
index is used. ~1.3 ms vs ~419 ms: ~320× faster on the query alone,
before counting the concurrency win from async + pooling.

## What the automated test asserts

```python
plan = "\n".join(...EXPLAIN (FORMAT TEXT)... WHERE species ILIKE $1 ...)
assert "specimens_species_trgm_idx" in plan   # the index is used
assert "Seq Scan" not in plan                 # not a sequential scan
```

It uses a **selective** pattern (`%sp4999%`, one matching row) so the
planner unambiguously prefers the index — a non-selective pattern that
matches 10% of the table could legitimately choose a seq scan, which
would make the test flaky for the wrong reason.

## Troubleshooting

| You see | Cause | Fix |
|---|---|---|
| `Seq Scan` for a ≥3-char pattern | extension or index missing | re-run `migrations/0001_search_index.sql` |
| `ERROR: operator does not exist: text % unknown` | `pg_trgm` not installed | `CREATE EXTENSION pg_trgm;` |
| `Seq Scan` only for short patterns | pattern < 3 chars can't form a trigram | expected; require ≥3 chars for indexed search |
| Index exists but unused | stale planner stats | `ANALYZE specimens;` |
