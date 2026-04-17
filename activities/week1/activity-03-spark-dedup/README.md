# Activity 3: Fix Duplicate Records in Spark Gold Layer

**Week:** 1 | **Day:** 3 | **Course alignment:** Agentic AI

## Problem Statement

A Spark job creates **duplicate records in the gold layer** every time it is re-run.

Root cause: the job uses `df.write.mode("append")` with no deduplication logic.
Re-running after a failure — or as part of a normal backfill — creates multiple
copies of every `(doc_id, chunk_id)` row. After N re-runs there are N × the rows.

## What Was Broken

`broken/spark_job.py` line 28:

```python
# No deduplication — every re-run appends the full silver dataset again
df.write.mode("append").parquet(gold_path)
```

## What Was Fixed

| Change | File | Why |
|--------|------|-----|
| Delta MERGE INTO on `(doc_id, chunk_id)` | `spark_job.py` | Atomic, ACID upsert — same key updates in-place, never duplicates |
| Parquet read-dedup-overwrite fallback | `spark_job.py` | No delta-spark needed for local dev; left_anti join removes replaced rows before union |
| `dropDuplicates` on incoming batch | `spark_job.py` | Delta MERGE fails if source DataFrame has duplicate composite keys |
| Schema validation | `spark_job.py` | Fast-fail with a clear error if `doc_id` or `chunk_id` columns are absent |
| Graceful empty-silver handling | `spark_job.py` | Missing silver path logs a warning and returns cleanly instead of crashing |

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for:
- Full data flow diagram
- Delta vs Parquet trade-off table
- Step-by-step AWS console guide (EMR, Glue, LocalStack)

## Running Locally

```bash
pip install -r requirements.txt

# parquet mode — no delta-spark needed
SILVER_PATH=./data/silver GOLD_PATH=./data/gold WRITE_FORMAT=parquet \
python spark_job.py
```

## Running Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Expected output:

```
tests/test_spark_job.py::TestParquetUpsert::test_initialises_gold_when_missing PASSED
tests/test_spark_job.py::TestParquetUpsert::test_idempotency PASSED
tests/test_spark_job.py::TestParquetUpsert::test_updates_existing_record PASSED
tests/test_spark_job.py::TestParquetUpsert::test_adds_new_records_without_touching_existing PASSED
tests/test_spark_job.py::TestParquetUpsert::test_empty_incoming_does_not_change_gold PASSED
tests/test_spark_job.py::TestRun::test_raises_on_missing_required_columns PASSED
tests/test_spark_job.py::TestRun::test_deduplicates_duplicate_keys_in_incoming_batch PASSED
tests/test_spark_job.py::TestRun::test_idempotency_end_to_end PASSED
tests/test_spark_job.py::TestRun::test_routes_to_upsert_delta_when_format_is_delta PASSED
tests/test_spark_job.py::TestRun::test_missing_silver_layer_logs_warning_without_raising PASSED
tests/test_spark_job.py::TestBrokenBehaviour::test_broken_append_creates_duplicates PASSED
```

## Key Design Decisions

**Why `(doc_id, chunk_id)` as composite key?**
A single document produces many chunks. `doc_id` alone is not unique; `chunk_id`
alone is not globally unique across documents. The pair is the minimal unique key
for a chunk in this pipeline.

**Why keep a Parquet fallback?**
Delta Lake adds a dependency and requires `_delta_log/` housekeeping in S3.
For local development, CI, or teams not yet on EMR/Databricks, the Parquet
fallback provides the same idempotency guarantee with no extra setup.

**Why `dropDuplicates` before the merge?**
If the silver layer itself contains two rows with the same `(doc_id, chunk_id)` —
from an upstream bug or a partial backfill — Delta MERGE INTO raises:
`"Spark cannot determine which row to use in the merge"`.
Deduplicating before the merge makes the job robust to upstream anomalies.

## PR Checklist

- [x] Broken version preserved in `broken/spark_job.py` with clear bug annotation
- [x] Fix applied in `spark_job.py` — Delta MERGE INTO + Parquet fallback
- [x] 11 tests written covering idempotency, updates, inserts, edge cases, regression
- [x] Architecture diagram + trade-offs in `docs/architecture.md`
- [x] AWS console setup guide (EMR, Glue, LocalStack) in `docs/architecture.md`
- [x] All config via environment variables — no hardcoded paths
- [ ] 2–5 min video walkthrough (before/after) — link to be added

## Notes

- Delta Lake MERGE INTO is the production path; Parquet fallback is for local dev.
- The `.count()` after `dropDuplicates` is intentionally kept for observability;
  remove it in throughput-critical pipelines (it triggers an extra Spark job).
- For a streaming version of this pipeline, Delta Lake's Structured Streaming
  with `foreachBatch` + MERGE INTO achieves the same idempotency at micro-batch level.
