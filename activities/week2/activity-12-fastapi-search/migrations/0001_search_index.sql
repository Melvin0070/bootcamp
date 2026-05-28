-- Activity 12: index the search column so /search stops doing a
-- sequential scan on every request.
--
-- The query in app.py is:
--   WHERE species ILIKE $1   (bound value is '%<term>%')
--   ORDER BY species
--
-- A plain btree on `species` only helps equality / prefix queries; a
-- leading-wildcard ILIKE like '%stego%' forces a sequential scan
-- because btree is left-anchored. pg_trgm's GIN index accelerates
-- arbitrary substring LIKE/ILIKE matches at sub-millisecond latency
-- on 500 k rows.
--
-- Apply with:
--   psql "$DATABASE_URL" -f migrations/0001_search_index.sql
--
-- Verify with:
--   EXPLAIN ANALYZE SELECT * FROM specimens WHERE species ILIKE '%sp4999%';
--
-- Expected: "Bitmap Index Scan on specimens_species_trgm_idx"
-- in the plan. If you see "Seq Scan" the extension isn't installed
-- or the index isn't built. See docs/explain-output.md.

BEGIN;

-- pg_trgm ships with Postgres but isn't enabled by default.
-- Safe to run on a database where it's already enabled.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- gin_trgm_ops is the operator class that teaches the GIN index how
-- to accelerate LIKE / ILIKE substring matches. (gist_trgm_ops also
-- works but is slower to query for our shape.)
CREATE INDEX IF NOT EXISTS specimens_species_trgm_idx
    ON specimens
    USING gin (species gin_trgm_ops);

-- Note: patterns shorter than 3 characters can't form a trigram, so
-- ILIKE '%ab%' falls back to a sequential scan. The app caps query
-- length at 128 and the UI should require >=3 chars for indexed search.

COMMIT;
