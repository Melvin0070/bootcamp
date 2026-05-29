# FossilRAG — Observability

Three signals, all serverless-native and $0 to author/test.

## Logs — structured `key=value`

Every component logs one line per event (`fossilrag.logging`), e.g.
`event=excavate q='…' k=5 hits=3 latency_ms=12.34`. Machine-parseable by
CloudWatch Logs Insights, readable in a terminal. A single filter
(`fields @timestamp, event, request_id | filter event = "http_request"`)
selects across the whole system.

### Correlation id

`RequestContextMiddleware` honours an inbound `X-Request-ID` (or generates one),
binds it to a context var, echoes it on the response, and stamps it on the
access log line — so a single request is traceable end to end.

## Metrics — CloudWatch EMF (no PutMetricData)

`fossilrag.observability.metrics.emit_metric` writes one Embedded Metric Format
log line; CloudWatch Logs auto-extracts the metric (namespace **`FossilRAG`**) —
no API calls, no extra IAM, identical in Lambda / container / local. The
request middleware emits `RequestLatencyMs` (+ count) dimensioned by `Endpoint`
for **every** route automatically, so latency/throughput metrics need zero
per-handler code. `build_emf` is a pure function and is unit-tested.

## Traces — AWS X-Ray

The worker, DLQ, and API Lambdas run with `tracing_config { mode = "Active" }`
(`infra/lambda.tf`). Full OpenTelemetry/ADOT instrumentation is the documented
next step — deliberately not vendored, so the tested surface stays runnable at
$0 (same honesty posture as the AOSS binding).

## Alarms + dashboard (`infra/monitoring.tf`)

One SNS topic (`alarm_email` to subscribe) gathers:

- **DLQ not empty** — poison messages exhausted their retries.
- **Lambda errors** — worker + API, any error in a 5-minute window.
- **API 5xx** and **API p99 latency** (> `api_latency_p99_threshold_ms`).

A CloudWatch dashboard plots queue/DLQ depth, Lambda invocations+errors, API
4xx/5xx/p99, and the custom EMF `RequestLatencyMs` per endpoint.

## Verification posture

Logs, EMF (`build_emf`/`emit_metric`), the middleware (correlation id + security
headers), and the API-key gate are unit-tested at $0 (TestClient, no DB). The
alarms/dashboard are `terraform validate`-clean. X-Ray + the live dashboards are
exercised only on a real deploy — not claimed as CI-verified.
