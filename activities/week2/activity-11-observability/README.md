# Activity 11: Add Observability to a Pipeline with Zero Monitoring

**Week:** 2 | **Day:** 11 | **Course alignment:** System Design Foundations / Claude Code in Action

## Problem Statement

The FossilRAG ingestion Lambda (the same `pipeline.py` Activity 10
deployed) runs on an S3 ObjectCreated trigger. It has **zero
operational signal**:

- Only the built-in Lambda metrics (`Invocations`, `Errors`, `Duration`)
- No per-stage breakdown — when output looks short, nothing tells you
  which of `read / normalise / write / upload` is at fault
- A bare `except: pass` returns `statusCode: 200` on every failure,
  so the built-in `Errors` counter stays at zero while data is missing
- No alarms — downstream BI discovers the outage on Monday morning
- No dashboard — the on-call SRE pivots between three Console tabs
- `print()` instead of structured logs, so Logs Insights cannot
  parse anything

## What I Fixed

- [x] **EMF custom metrics**: `Latency` and `Errors` per stage, plus
      `RowsIngested`, `RowsDropped`, `Invocations`, `InvocationFailures`
      summary metrics — all in the `FossilRAG/Pipeline` namespace
- [x] **CloudWatch Alarms** (CloudFormation): per-stage errors + per-stage
      p99 latency (each pinned to its `Stage` dimension), empty-output,
      plus a composite that gates the pager
- [x] **CloudWatch Dashboard**: invocations vs failures, per-stage
      errors, per-stage p50/p99 latency, rows ingested vs dropped,
      active alarms — last 24h at a glance
- [x] **Structured `event=key=value` logs** so Logs Insights queries
      work without a custom grok pattern
- [x] **`correlation_id` / `request_id`** propagated through every
      stage record so an on-call can pivot from one log line to the
      full invocation timeline

## Acceptance Criteria

- ✅ Per-stage failure rate and latency metrics are visible in CloudWatch
      (`FossilRAG/Pipeline` namespace, `Stage` dimension)
- ✅ The failure-rate alarm fires within 3–5 minutes of a sustained
      failure (`EvaluationPeriods=5`, `DatapointsToAlarm=3`, 1-min period)
- ✅ Dashboard shows last 24h of pipeline health at a glance
- ✅ Logs Insights can parse the structured logs without custom grok
      (queries shown in [`docs/logs-insights-queries.md`](docs/logs-insights-queries.md))

## What Was Fixed

| # | Anti-pattern (broken) | Fix |
|---|---|---|
| 1 | Only Lambda built-in metrics; no per-stage signal | EMF emitter (`metrics.py`) writes one JSON record per stage: `Latency` + `Errors` with `Stage` dimension |
| 2 | `print()` everywhere; Logs Insights can't parse | `logger.info("event=stage_done stage=%s success=%s latency_ms=%.2f ...")` — `key=value` shape Insights parses for free |
| 3 | `except: pass` returns 200 on every failure | `stage_timer` re-raises; the Lambda Invocation marks as errored AND a per-stage `Errors` metric ticks |
| 4 | No alarms; downstream BI discovers outages on Monday | Per-stage errors + per-stage p99-latency alarms (each pinned to its `Stage` dimension) + empty-output + a composite, in `infra/observability.yaml` |
| 5 | No dashboard; on-call pivots between Console tabs | `AWS::CloudWatch::Dashboard` with 5 widgets (invocations, errors, latency, rows, active alarms) — 24h view |
| 6 | "Empty parquet" silent failure invisible | `RowsIngested` metric + alarm with `TreatMissingData=breaching` — also catches "Lambda never ran" |
| 7 | No correlation key across log lines | `request_id` propagated as an EMF property (not a dimension — explained below) |
| 8 | `PutMetricData` would add API calls in the hot path | EMF piggybacks on the log stream — zero extra network, zero extra IAM, zero extra cost |
| 9 | Latency printed as text, not a metric | `stage_timer` emits `Latency` with unit `Milliseconds`; alarm uses `ExtendedStatistic=p99` (not Average — averaging hides spikes) |
| 10 | Average-based latency alarms hide outliers | Explicit `ExtendedStatistic: p99` on the latency alarm; test pins this so a refactor can't silently downgrade it |
| 11 | Cascading alarms page on-call 3× for one cause | Composite alarm fans 3 sub-alarms into one notification channel |

## Architecture (one-liner)

```
S3 event → Lambda → EMF JSON to stdout → CloudWatch Logs → EMF auto-extract
                                                       │
                                                       ├─▶ CloudWatch Metrics
                                                       │     ├─▶ Dashboard
                                                       │     └─▶ Alarms ──▶ SNS
                                                       └─▶ Logs Insights queries
```

Full before/after and trade-offs in [`docs/architecture.md`](docs/architecture.md).
Incident response in [`docs/runbook.md`](docs/runbook.md).
Saved Logs Insights queries in [`docs/logs-insights-queries.md`](docs/logs-insights-queries.md).

## How to Run Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v --cov=. --cov-report=term-missing --cov-fail-under=85
cfn-lint infra/observability.yaml
```

Expected: **39 tests passing**, **96% coverage**, **cfn-lint clean**.

- 21 EMF emitter behaviour tests (`tests/test_metrics.py`)
- 9 infra-template shape assertions (`tests/test_infra.py`) — alarms
  present, p99 not average, dashboard JSON valid, dashboard
  references every required metric
- 14 pipeline behaviour + EMF-contract tests + broken-baseline
  regression (`tests/test_pipeline.py`) — moto-stubbed S3, the bare
  `except: pass` anti-pattern is still present in `broken/`

## How to Apply the Observability Stack

```bash
aws cloudformation deploy \
  --template-file infra/observability.yaml \
  --stack-name fossilrag-pipeline-observability \
  --parameter-overrides \
      FunctionName=fossilrag-ingestion-staging \
      AlarmNotificationTopic=arn:aws:sns:us-east-1:<acct>:fossilrag-oncall \
      FailureRateThreshold=3 \
      P99LatencyThresholdMs=30000
```

The template is idempotent — re-running on the same parameters is a
no-op. Tuning the thresholds at staging vs prod is just a parameter
change.

## Layout

```
activity-11-observability/
├── broken/
│   └── pipeline.py            # "before" — bare except + print() + zero metrics
├── metrics.py                 # EMF emitter + stage_timer context manager
├── pipeline.py                # fixed pipeline, EMF-instrumented
├── infra/
│   └── observability.yaml     # alarms + composite alarm + dashboard
├── tests/
│   ├── test_metrics.py        # EMF shape assertions (21 tests)
│   ├── test_infra.py          # CFN template shape (9 tests)
│   └── test_pipeline.py       # pipeline + EMF contract + broken-baseline (14 tests)
├── docs/
│   ├── architecture.md        # before/after, EMF rationale, edge cases
│   ├── runbook.md             # incident response per alarm
│   └── logs-insights-queries.md  # cookbook for on-call
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

The CI workflow lives at the repo root (the only place GitHub Actions
discovers it):

```
.github/workflows/activity-11-ci.yml
```

## PR Checklist

- [x] EMF emitter (`metrics.py`) — zero-extra-API-call custom metrics
- [x] Per-stage `Latency` + `Errors`, summary `RowsIngested` / `RowsDropped`
- [x] CloudFormation alarms (failure-rate / p99-latency / empty-output / composite)
- [x] CloudWatch dashboard with 5 widgets, 24h view, JSON validated in tests
- [x] Structured `event=key=value` logs + `request_id` correlation key
- [x] Anti-patterns preserved in `broken/pipeline.py`
- [x] 39 pytest assertions cover anti-patterns, fixes, EMF contract, CFN shape
- [x] `docs/architecture.md`, `docs/runbook.md`, `docs/logs-insights-queries.md`
- [x] Path-filtered CI workflow at `.github/workflows/activity-11-ci.yml`
- [ ] 2–5 min video walkthrough (before/after) — to add

## Notes

**Why EMF and not `PutMetricData`.** PutMetricData would add a
synchronous network call inside the hot path of every Lambda
invocation, plus an extra IAM permission, plus a per-call charge.
EMF piggybacks on the log stream we already pay for — the same JSON
line is parseable by humans (in Logs Insights), by tests (parse
stdout, assert shape), and by the CloudWatch metrics backend.

**Why `request_id` is a property, not a dimension.** CloudWatch
charges per metric series (= per unique dimension combination). If
`request_id` were a dimension we'd create one series per invocation
— ~$0.30/metric/month × 100k invocations = bankruptcy. As a property
it shows up in Logs Insights for correlation but doesn't multiply
the metric count. There's a dedicated regression test for this
(`test_request_id_is_NOT_a_dimension`) because it's the kind of mistake
that's invisible until the bill arrives.

**Why p99 on the latency alarm, not Average.** Averages hide tail
latency by definition. A 95th-percentile spike from 50 ms to 5 s
moves the average by less than a millisecond per spike but is
exactly what the consumer feels. `ExtendedStatistic: p99` on the
alarm + a regression test pinning it (`test_latency_alarm_uses_p99_not_average`)
prevents a "let's simplify this" refactor from silently downgrading
the signal.

**Why one composite alarm.** Nine independent alarms (errors + p99
latency per stage, plus empty-output) means up to nine pages for one
root cause (a slow stage usually causes failures and empty output
too). The composite fans them all into one PagerDuty fire-and-forget,
while the sub-alarms still exist for the dashboard panel and the
runbook drill-down. cfn-lint flags an explicit
`DependsOn` as redundant when an `AlarmRule` references the children;
we let the `Ref`s in `AlarmRule` carry the ordering and ship a clean
template.

**Why `TreatMissingData=breaching` on empty-output.** This is the bit
that catches "the Lambda didn't run at all" — S3 event source
mapping detached, IAM role broken, function deleted. The cost is a
false page on the very first deploy before any data arrives; we
accept that one-time pain in exchange for not silently missing a
real outage.

**Why 85% coverage, not 100%.** Coverage is a coarse signal. 85% is
reachable on a small module without test theatre and catches the
"added a function, no test" case — which is the failure mode
coverage gates actually prevent. Current coverage is 96%.
