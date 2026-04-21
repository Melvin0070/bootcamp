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

## What Was Fixed

| # | Anti-pattern (broken) | Fix applied | Impact |
|---|---|---|---|
| 1 | `retries=0` (default) | `retries=3` + `retry_exponential_backoff=True` + 30s base / 10min cap | Survives transient Lambda timeouts and cold-start delays |
| 2 | No `on_failure_callback` | Composite callback: Slack webhook + email via smtplib | On-call notified after all retries exhausted, with DAG/task/run context |
| 3 | No `execution_timeout` | `timedelta(seconds=LAMBDA_TIMEOUT_SECONDS + 60)` | Prevents tasks hanging forever on Lambda connectivity issues |
| 4 | `print()` logging | JSON formatter on stdlib `logging` | CloudWatch Insights / Datadog can query `dag_id`, `task_id`, `run_id` fields |
| 5 | Hardcoded function names + region | All config via env vars; `.env.example` documents every variable | One DAG file works across dev / staging / prod |
| 6 | `FunctionError` unchecked | `if "FunctionError" in response: raise RuntimeError(…)` | Lambda crash (HTTP 200 + error payload) now surfaces as a retriable failure |

## How to Run Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Expected: **34 tests passing** across 6 test classes.

## PR Checklist

- [x] Fix applied in `broken/dag.py` → working `dag.py` committed
- [x] `.env.example` documents all environment variables
- [x] 34 pytest assertions cover both broken anti-patterns and all fixes
- [x] `docs/architecture.md` — before/after diagrams, trade-off table, edge cases
- [ ] 2–5 min video walkthrough (before/after)

## Notes

**Key insight — Lambda FunctionError:**
Lambda returns HTTP 200 even when the function itself raises an exception. The broken DAG
only checked `response["StatusCode"] != 200`, so a Lambda timeout or unhandled exception
looked like success. The fix checks `"FunctionError" in response` before reading the payload.

**Alert timing:**
`on_failure_callback` fires *after* all retries are exhausted, not on first failure.
This avoids alert fatigue for expected transient retries while still paging on-call
for genuine failures.

**Retry backoff schedule:**
`retry_exponential_backoff=True` with `retry_delay=timedelta(seconds=30)` gives:
30s → 60s → 120s (capped at `max_retry_delay=timedelta(minutes=10)`).
