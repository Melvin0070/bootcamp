# FossilRAG — Runbook

Operational guide for the full shipped system: the SQS-driven ingestion
pipeline (worker + DLQ), the retrieval / embedding / mutation API, and
observability (metrics, alarms, dashboard).

## Components & triggers

| Component | Trigger | Reads | Writes |
|-----------|---------|-------|--------|
| Ingestion Lambda (`ingest.handler`) | S3 `ObjectCreated:*` on the raw bucket | raw object | silver JSON (`s3://<silver>/silver/<doc_id>.json`) |
| Retrieval API (`api.app`) | HTTP | pgvector | — |

## Configuration (env)

All via `pydantic-settings` (`FOSSILRAG_*`; `DATABASE_URL`/`AWS_ENDPOINT_URL`
also read unprefixed). See `.env.example`.

| Var | Purpose |
|-----|---------|
| `DATABASE_URL` | Postgres+pgvector DSN (API) |
| `FOSSILRAG_SILVER_BUCKET` | destination bucket for silver JSON (ingestion Lambda) |
| `FOSSILRAG_SILVER_PREFIX` | key prefix (default `silver`) |
| `FOSSILRAG_AWS_REGION` / `AWS_ENDPOINT_URL` | region; endpoint override for LocalStack |
| `FOSSILRAG_EMBED_PROVIDER` / `_MODEL` / `_DIM` | embedding provider + provenance |

## Ingestion behaviour

- **Idempotent by content addressing.** `doc_id = sha256(full_s3_key + text)`;
  the silver key is `silver/<doc_id>.json`, so a redelivered S3 event
  overwrites the same object with identical bytes — no duplicates, no
  corruption. (A *compute-skip* ledger to avoid re-extracting at all is the
  Self-Healing Idempotency mutation, PR3.)
- **Failure handling.** A per-record extraction failure (corrupt/unsupported
  body) is normalised to a `ValueError`, collected, and the invocation
  re-raises at the end so the platform retries and routes exhausted retries to
  an SQS DLQ (`infra/sqs.tf` redrive, `maxReceiveCount=5`). A *structurally malformed* event record
  (missing bucket/key) is logged and skipped (retry can't fix it).

## Common procedures

- **Reprocess a document:** re-upload it to the raw bucket (or replay the S3
  event). Content-addressing makes this safe to repeat.
- **Inspect a fossil's provenance:** read `silver/<doc_id>.json` — it carries
  `filename`, `source_uri`, `content_type`, `uploaded_at`, and the extractor.
- **SQS ingestion worker (`worker.sqs.sqs_handler`):** drains
  `{"bucket","key"}` tasks with retry/backoff + idempotent skip, returning
  `batchItemFailures` so only failed messages are retried (then DLQ'd after the
  queue's `maxReceiveCount`). Redelivered already-processed objects are no-ops.
- **Dead letters (`worker.dlq.dlq_handler`):** logs each poison message
  (`event=dead_letter`) for alarms/Logs Insights. To **redrive**, move messages
  from the DLQ back to the source queue after fixing the root cause (a
  permanently-bad object — unsupported type / deleted key — will simply DLQ
  again, which is the signal to drop or fix it).

## Local development

- API + pgvector: `pip install -e .` (so `make seed` can import `fossilrag`),
  `make up`, then `make seed` / `curl /excavate`. `make down`.
- AWS-service behaviour is exercised in tests via **moto** (`$0`, no account).
  The compose demo (PR12) uses **LocalStack** for S3/DynamoDB/SQS — note its
  free tier needs a `LOCALSTACK_AUTH_TOKEN` and is non-commercial.
- The dev box has no Docker, so the compose/pgvector paths are **CI-verified**.

## Health & observability

- `GET /healthz` — pool + store readiness. Structured `event=...` logs
  throughout, with a per-request correlation id (`X-Request-ID`) + CloudWatch
  EMF metrics and alarms/dashboard (`infra/monitoring.tf`; see
  [`observability.md`](observability.md)).
