# Activity 6: Fix a Schema-Breaking Scala ETL

**Week:** 1 | **Day:** 6 | **Course alignment:** AWS Technical Essentials

## Problem Statement

A Scala ETL pipeline **breaks downstream consumers** whenever the input Parquet schema changes (e.g., a new column is added upstream). No backward compatibility strategy exists.

## What to Fix

- [ ] Implement **schema merging** (`mergeSchema = true` in Spark Parquet reads)
- [ ] Add **default values** for new nullable columns so old readers don't break
- [ ] Write a schema validation step that warns (not fails) on new fields
- [ ] Document the schema evolution policy

## Acceptance Criteria

- Adding a new column upstream does not break downstream consumers
- Old data without the new column reads cleanly with null/default values
- Schema changes are logged and visible

## PR Checklist

- [ ] Fix applied in `broken/` → working code committed
- [ ] Clear PR description (what was broken, what you changed, why)
- [ ] 2–5 min video walkthrough (before/after)

## Notes

_Add your findings, decisions, and observations here as you work._
