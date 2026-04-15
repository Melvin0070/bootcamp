# Architecture: Document Ingestion Pipeline

## Before (Broken)

```
S3 Upload
    │
    ▼
SQS Queue ──────────────────────── (no DLQ — failed messages retry forever)
    │  BatchSize=1
    │  VisibilityTimeout=30s
    ▼
Lambda (Timeout=3s, no concurrency limit)
    │
    │  ❌ Processes ONE record at a time
    │  ❌ Any exception = entire batch retried
    │  ❌ Hardcoded bucket names
    │  ❌ No logging
    │  ❌ Returns 200 even on failure
    ▼
S3 Silver Bucket
```

**Failure mode:** A 50KB document takes ~4s to process → Lambda times out →
SQS re-delivers the message → Lambda times out again → repeat until the
4-day message retention expires. No alert fires. No one knows.

---

## After (Fixed)

```
S3 Upload
    │
    ▼
SQS Queue ──────────────────────────────────► DLQ
    │  BatchSize=10                           (after 3 retries)
    │  BatchWindow=30s                            │
    │  VisibilityTimeout=360s                     ▼
    │  maxReceiveCount=3                   CloudWatch Alarm
    ▼                                      (fires on 1st DLQ msg)
Lambda (Timeout=60s, ReservedConcurrency=10)
    │
    ├── Record 0: ✅ success
    ├── Record 1: ✅ success
    ├── Record 2: ❌ error ──────────────► batchItemFailures: [msg-2]
    ├── Record 3: ✅ success                  │
    └── ...                                   └── SQS retries ONLY msg-2
    │                                             (not the whole batch)
    ▼
DynamoDB Idempotency Table
    │  (already processed? skip)
    ▼
S3 Silver Bucket
```

---

## Key Design Decisions & Trade-offs

| Decision | Choice | Alternative | Why |
|----------|--------|-------------|-----|
| Concurrency cap | Reserved = 10 | Provisioned concurrency | Reserved is cheaper; provisioned reduces cold starts but costs more. 10 is safe for dev. |
| Batch size | 10 messages | 1 (old) or 100+ | 10 balances latency (30s wait) vs cost (10× fewer invocations than BatchSize=1). |
| Batch window | 30s | 0 (real-time) | 30s fills batches more efficiently. For a real-time use case, set to 0 and accept higher Lambda cost. |
| DLQ retry count | maxReceiveCount=3 | 1 (aggressive) or 10 (lenient) | 3 handles transient failures (cold start, brief S3 blip) without infinite retry storms. |
| Idempotency storage | DynamoDB | S3 manifest | DynamoDB is faster (single-digit ms reads); S3 is cheaper at very large scale but slower. |
| Fail open on DynamoDB error | Yes (process anyway) | Fail closed (skip) | Failing open means a document might be re-processed, but it won't be silently lost. |

---

## VisibilityTimeout Rule

> **Always set VisibilityTimeout ≥ 6 × Lambda timeout.**

If VisibilityTimeout is shorter than the Lambda execution time, SQS re-delivers
the message while Lambda is still working on it — causing duplicate processing.

```
Lambda timeout = 60s
VisibilityTimeout = 360s  (6×)  ✅

Lambda timeout = 60s
VisibilityTimeout = 30s         ❌ — message reappears after 30s, Lambda still running
```

---

## Partial Batch Response — The Most Missed Pattern

Without `ReportBatchItemFailures`:
```
Batch: [A, B, C, D, E]  — D fails
SQS retries: [A, B, C, D, E]  ← A, B, C, E get reprocessed unnecessarily
```

With `ReportBatchItemFailures`:
```
Batch: [A, B, C, D, E]  — D fails
Lambda returns: { batchItemFailures: [{ itemIdentifier: "msg-D" }] }
SQS retries: [D]  ← only the failing message
```

This is critical for correctness at scale. Without it, every transient failure
causes previously-successful records to be duplicated.

---

## Cost Comparison (illustrative)

| Scenario | BatchSize=1 | BatchSize=10 (fixed) |
|----------|------------|----------------------|
| 10,000 messages/day | 10,000 invocations | ~1,000 invocations |
| Lambda invocation cost | ~$0.02 | ~$0.002 |
| Duration cost (60s avg) | higher | same total compute |
| Relative saving | baseline | ~90% fewer invocations |
