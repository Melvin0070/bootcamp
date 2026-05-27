# Activity 11 — Architecture

## Before / after

```
Before (broken/pipeline.py):

  S3 ObjectCreated ──▶ Lambda ──▶ print() ──▶ CloudWatch Logs (plain text)
                         │
                         └─▶ bare except: pass ─▶ statusCode: 200
                                                    │
                                                    └─▶ silent failure;
                                                        BI dashboard stale
                                                        on Monday morning

  Signals visible to on-call:
    Lambda built-in Invocations / Errors / Duration only.
    Errors counter stays at zero because exceptions are swallowed.
    No per-stage breakdown, no row-count gauge, no alarms.


After (pipeline.py + infra/observability.yaml):

  S3 ObjectCreated ──▶ Lambda ─┬─▶ structured key=value logs ─┐
                               │                              ▼
                               └─▶ EMF JSON records ──▶ CloudWatch Logs
                                                              │
                                       ┌──────────────────────┤
                                       │ Logs Insights        │ EMF auto-extract
                                       ▼                      ▼
                               docs/logs-insights-queries.md  CloudWatch Metrics
                                                              (FossilRAG/Pipeline)
                                                              │
                                                              ├─▶ Dashboard
                                                              │   fossilrag-<fn>
                                                              │
                                                              └─▶ Alarms
                                                                  ├─ failure-rate
                                                                  ├─ p99-latency
                                                                  ├─ empty-output
                                                                  └─ composite ──▶ SNS
```

## Why EMF, not PutMetricData

CloudWatch supports two ways to emit custom metrics:

| Aspect | `PutMetricData` API | Embedded Metric Format (EMF) |
|---|---|---|
| Network call per metric | yes — synchronous, in the hot path | no — piggybacks on the existing log stream |
| Extra IAM | `cloudwatch:PutMetricData` | none beyond `logs:PutLogEvents` |
| Cost | per-API-call charge | free (already paying for the log stream) |
| Testability | needs moto or a real CloudWatch stub | parse stdout — one assertion per metric |
| Failure mode | a metric call can fail independently of the work | metric and log share the same fate |
| Lambda cold-start impact | adds a boto3 client init | none |

EMF wins on every axis for a Lambda-shaped workload. The trade-off is
that the metric only exists once CloudWatch Logs has ingested and
parsed the record, which can lag by a few seconds — irrelevant for
alarms with a 1- or 5-minute period.

## Metric catalogue

| Metric | Unit | Dimensions | Source |
|---|---|---|---|
| `Latency` | Milliseconds | `Stage ∈ {read, normalise, write, upload}` | `metrics.stage_timer` |
| `Errors` | Count | `Stage ∈ {read, normalise, write, upload}` | `metrics.stage_timer` (0 or 1 per invocation) |
| `RowsIngested` | Count | `Stage=summary` | `pipeline.handler` (per invocation) |
| `RowsDropped` | Count | `Stage=summary` | `pipeline.handler` (per invocation) |
| `Invocations` | Count | `Stage=summary` | `pipeline.handler` (always 1, used as denominator) |
| `InvocationFailures` | Count | `Stage=summary` | `pipeline.handler` (only on top-level exception) |

`Stage` is the only dimension. `request_id` is emitted as a **property**
(not a dimension) so it shows up in Logs Insights for correlation but
does not create one CloudWatch series per invocation.

## Alarm catalogue

| Alarm | Threshold | Window | Notes |
|---|---|---|---|
| `failure-rate` | `Errors Sum > 3` | 3 of 5 datapoints, 1 min | Per-stage errors; sums across stages. |
| `p99-latency` | `Latency p99 > 30000 ms` | 5 min | Extended statistic (averages would hide spikes). |
| `empty-output` | `RowsIngested Sum < 1` | 5 min | `TreatMissingData=breaching` so a no-run page-fires too. |
| `composite` | any of the above | n/a | Single fan-in for PagerDuty / Slack. |

Thresholds are CloudFormation parameters so staging and prod can use
different values without forking the template.

## Trade-offs

**EMF vs aws-embedded-metrics package.** The package adds ~5 MB to the
Lambda deploy bundle and 30 ms to cold start, in exchange for letting
you skip a 50-line emitter. For a hot-path pipeline that's a bad
trade; the emitter in `metrics.py` is small enough to audit in one
sitting.

**One namespace vs per-component namespaces.** CloudWatch charges per
custom metric, not per namespace, so the split is cosmetic. One
namespace makes the dashboard wildcard easier and keeps the IAM
allow-list short.

**Composite alarm vs flat alarm list.** Three separate alarms means
three pages at 2 AM for one cascading failure. The composite alarm
collapses them into one. Sub-alarms still exist for the dashboard
panel and post-incident drill-down — the composite just gates the
pager.

**`TreatMissingData=breaching` on empty-output.** This is the bit that
catches "the Lambda didn't run at all" (e.g. S3 event source mapping
deleted, IAM role broken). The cost is a false page on the very first
deploy before any data has arrived; the alarm's `EvaluationPeriods=1`
+ a 5-minute period makes that a single 5-minute window of pain,
which we accept in exchange for not missing a real outage.

## Edge cases handled

- **Schema drift.** If upstream renames `Species` → `species_name`,
  `normalise()` raises, `stage_normalise_done.Errors=1`,
  `failure-rate` fires within ~3 minutes. The `pipeline_failed`
  record carries the request_id so the Logs Insights query in
  `docs/logs-insights-queries.md` returns the exception in one click.
- **Empty CSV.** Stages all succeed, but `RowsIngested=0`. The
  `empty-output` alarm catches this within 5 minutes — the silent
  failure mode that the broken baseline missed.
- **Lambda cold start.** Module-level boto3 client survives warm
  invocations; the per-stage Latency metric makes the cold-vs-warm
  split visible in the dashboard so we can decide whether to
  provisioned-concurrency the function.
- **Burst traffic.** Every invocation emits exactly one EMF block per
  stage — bounded log volume per invocation. No risk of a log
  explosion from a runaway loop.
- **Concurrent invocations.** Each invocation gets a fresh
  `request_id` and emits independent EMF records; CloudWatch
  aggregates them server-side. No locking, no per-process state.

## What's NOT in this activity

- Distributed tracing (X-Ray) — out of scope; the
  `request_id` correlation key would carry the trace_id when we add it.
- Anomaly detection — CloudWatch supports it on these metrics; for
  staging it's a noisy first signal, so we ship static thresholds
  first and tune later.
- Custom SLO / SLI burn-rate alerts — appropriate once we have a few
  weeks of baseline data to set realistic targets.
