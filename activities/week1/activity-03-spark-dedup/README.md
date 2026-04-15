# Activity 3: Fix Duplicate Records in Spark Gold Layer

**Week:** 1 | **Day:** 3 | **Course alignment:** Agentic AI

## Problem Statement

A Spark job creates **duplicate records in the gold layer** every time it is re-run.

Root cause: the job does a full overwrite with no deduplication logic — re-runs create multiple copies of the same `(doc_id, chunk_id)`.

## What to Fix

- [ ] Implement **upsert logic** using `doc_id` + `chunk_id` as composite keys
- [ ] Use Delta Lake merge / `MERGE INTO` or equivalent idempotent write strategy
- [ ] Make the pipeline safe to re-run at any time without data duplication

## Acceptance Criteria

- Running the job twice produces the same output as running it once
- Existing records are updated in-place; no duplicates in the gold layer

## PR Checklist

- [ ] Fix applied in `broken/` → working code committed
- [ ] Clear PR description (what was broken, what you changed, why)
- [ ] 2–5 min video walkthrough (before/after)

## Notes

_Add your findings, decisions, and observations here as you work._
