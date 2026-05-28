-- Activity 12: minimal specimens schema for /search.
-- Mirrors the columns the FossilRAG ingestion pipeline writes.

CREATE TABLE IF NOT EXISTS specimens (
    id      SERIAL PRIMARY KEY,
    species TEXT NOT NULL,
    age_mya NUMERIC,
    notes   TEXT
);
