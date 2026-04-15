# Activity 8: Make the Embedding Pipeline Idempotent

**Week:** 2 | **Day:** 8 | **Course alignment:** System Design Foundations

## Problem Statement

The embedding generation pipeline re-embeds **all chunks on every run**, wasting compute and creating duplicate vectors.

## What to Fix

- [ ] Track processed chunks using a **DynamoDB table** or **S3 manifest** (keyed by `chunk_id`)
- [ ] At the start of each run, **skip chunks already in the manifest**
- [ ] On successful embedding, **write the chunk_id** to the manifest
- [ ] Handle partial failures: a chunk that failed mid-run should be retried next run

## Acceptance Criteria

- Re-running the pipeline does not re-embed already-processed chunks
- A new chunk is embedded exactly once
- Failed chunks are retried on the next run

## PR Checklist

- [ ] Fix applied in `broken/` → working code committed
- [ ] Clear PR description (what was broken, what you changed, why)
- [ ] 2–5 min video walkthrough (before/after)

## Notes

_Add your findings, decisions, and observations here as you work._
