# Activity 5: Fix a Silent-Failing Airflow DAG

**Week:** 1 | **Day:** 5 | **Course alignment:** AWS Technical Essentials

## Problem Statement

An Airflow DAG **fails silently** when a downstream Lambda times out:
- No retries — one failure = the whole run is lost
- No alerts — failures go unnoticed until data is missing
- No structured logging — impossible to diagnose what happened

## What to Fix

- [ ] Add **retries** with **exponential backoff** to Lambda-calling tasks
- [ ] Wire up **Slack or email alerts** on task failure
- [ ] Add **structured logging** (JSON logs with task name, run ID, timestamp)
- [ ] Set appropriate **timeout thresholds** per task

## Acceptance Criteria

- Transient Lambda timeouts auto-retry before alerting
- On persistent failure, an alert fires with enough context to act on
- All task logs are structured and parseable

## PR Checklist

- [ ] Fix applied in `broken/` → working code committed
- [ ] Clear PR description (what was broken, what you changed, why)
- [ ] 2–5 min video walkthrough (before/after)

## Notes

_Add your findings, decisions, and observations here as you work._
