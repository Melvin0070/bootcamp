# Activity 5 — Airflow DAG Silent-Failure Fix

## Problem Diagnosis

The original DAG invoked two AWS Lambda functions in sequence with zero resilience
mechanisms. Any transient Lambda timeout, cold-start delay, or throttle would kill
the entire daily run silently — no retry, no alert, no structured log to trace the failure.

| Anti-pattern | Symptom | Root cause |
|---|---|---|
| No `retries` | One Lambda timeout = whole run lost | Default `retries=0` in Airflow |
| No `on_failure_callback` | Data missing hours later | No alert wired to DAG or tasks |
| No `execution_timeout` | Task hangs indefinitely | Lambda connection issues can block forever |
| `print()` instead of `logging` | Unqueryable noise in CloudWatch | No structured log fields |
| Hardcoded function names | Breaks in staging/prod | No parameterisation |
| `FunctionError` unchecked | Silent Lambda crash looks like success | Lambda returns HTTP 200 even when function raises |

---

## Architecture: Before vs After

### Before (broken)

```
Airflow Scheduler
        │
        ▼
┌───────────────────┐
│ extract_fossils   │  ← no retries, no timeout
└───────────────────┘
        │ fails silently
        ▼
   ✗ run marked failed
   ✗ no alert fired
   ✗ unstructured print() in logs
   ✗ next task never runs
```

### After (fixed)

```
Airflow Scheduler
        │  daily @daily
        ▼
┌─────────────────────────────────────────────┐
│  extract_fossils                            │
│  retries=3 · exp backoff 30s→60s→120s       │
│  execution_timeout = Lambda max + 60s       │
│  structured JSON logs: dag/task/run_id      │
└─────────────────────────────────────────────┘
        │ on persistent failure (retries exhausted)
        ▼
┌──────────────────────────────────┐
│  on_failure_callback             │
│  ├── Slack webhook (requests)    │
│  └── Email (smtplib)             │
│  Both channels tried in parallel │
└──────────────────────────────────┘
        │ on success (≤ 3 retries)
        ▼
┌─────────────────────────────────────────────┐
│  transform_fossils                          │
│  (same retry + alert + timeout config)      │
└─────────────────────────────────────────────┘
        │
        ▼
┌──────────────────┐
│  notify_complete │
│  JSON log: done  │
└──────────────────┘
```

---

## Retry Mechanics

Airflow's `retry_exponential_backoff=True` implements the following schedule:

```
Attempt 1  → fails
  wait: retry_delay × 2^0 = 30s
Attempt 2  → fails
  wait: retry_delay × 2^1 = 60s
Attempt 3  → fails
  wait: retry_delay × 2^2 = 120s (capped at max_retry_delay=10min)
Attempt 4  → final failure → on_failure_callback fires
```

The `max_retry_delay` cap prevents indefinite delays when retry counts are higher.

---

## Lambda FunctionError vs HTTP Error

This is a subtle AWS SDK behaviour the broken DAG missed entirely:

```
Lambda HTTP 200 + Payload StatusCode 200 → success
Lambda HTTP 200 + FunctionError header   → function raised an exception (❌ broken DAG misses this)
Lambda HTTP 429                          → throttled (→ retry)
Lambda HTTP 500+                         → Lambda service error (→ retry)
```

The fixed DAG checks `response["FunctionError"]` before reading the payload, so a Lambda
timeout or unhandled exception is treated as a retriable failure rather than a silent success.

---

## Trade-off Table

| Decision | Chosen | Alternative | Reasoning |
|---|---|---|---|
| Retry count | 3 | 1 / 5 | 3 covers most transient issues; 5 risks stacking up for a systemic fault |
| Backoff strategy | Exponential (30s base) | Linear fixed 60s | Exponential avoids hammering a struggling Lambda; cap prevents hour-long delays |
| Max retry delay | 10 min | 30 min / none | Balances giving Lambda breathing room vs blocking next DAG run |
| Alert channels | Slack + email | PagerDuty / SNS | Slack = immediate visibility; email = audit trail; both attempted independently |
| Alert timing | After all retries exhausted | On first failure | Avoids alert fatigue for expected transient retries |
| Execution timeout | Lambda max + 60s buffer | Fixed 15 min | Lambda max is 15 min; 60s covers SDK overhead + cold starts without arbitrary margin |
| Logging format | JSON (one line/record) | Plain text | Machine-parseable → CloudWatch Insights, Datadog, OpenSearch all ingest directly |
| Lambda ARN env var | `os.environ.get(…, "")` | `os.environ[…]` | Fail at invocation time (not at DAG parse time) for better Airflow scheduler stability |

---

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `EXTRACT_LAMBDA_ARN` | Yes | — | ARN of the extraction Lambda |
| `TRANSFORM_LAMBDA_ARN` | Yes | — | ARN of the transform Lambda |
| `SLACK_WEBHOOK_URL` | No | `""` | Slack incoming webhook URL; omit to disable |
| `ALERT_EMAIL` | No | `""` | On-call email; omit to disable |
| `SMTP_HOST` | No | `localhost` | SMTP relay host |
| `SMTP_PORT` | No | `25` | SMTP port |
| `AWS_REGION` | No | `us-east-1` | Region for boto3 Lambda client |
| `LAMBDA_TIMEOUT_SECONDS` | No | `900` | Mirrors Lambda's max timeout; Airflow timeout = this + 60s |

---

## Structured Log Schema

Every log record emits a single JSON line:

```json
{
  "timestamp": "2024-03-15T10:23:45.123Z",
  "level": "INFO",
  "logger": "dag",
  "dag_id": "fossil_pipeline",
  "task_id": "extract_fossils",
  "run_id": "scheduled__2024-03-15T00:00:00+00:00",
  "message": "Lambda succeeded"
}
```

This schema is directly ingestible by CloudWatch Logs Insights:

```sql
fields @timestamp, task_id, level, message
| filter level = "ERROR"
| sort @timestamp desc
```

---

## Edge Cases Handled

| Case | Behaviour |
|---|---|
| Lambda cold start delay (1–5s) | Absorbed by `retry_delay=30s` base |
| Lambda throttle (HTTP 429) | Raises → retried with backoff |
| Lambda function raised exception | `FunctionError` detected, RuntimeError raised → retried |
| Lambda connection timeout | `execution_timeout` forces task failure → retried |
| Slack webhook unreachable | Logged as WARNING; email still attempted |
| Email SMTP unreachable | Logged as WARNING; Slack still attempted |
| `EXTRACT_LAMBDA_ARN` not set | `ValueError` raised at invocation with clear message |
| Task succeeds after retry 2 | No alert fired; only fires after all retries exhausted |
