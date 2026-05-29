# FossilRAG — Threat Model (STRIDE)

Scope: the serverless retrieval/enrichment system — API (Lambda behind API
Gateway), the SQS→worker ingestion path, S3 medallion buckets, DynamoDB
(idempotency + prompt cache), pgvector, Bedrock, and the React UI (nginx).
Trust boundary: the public internet → API Gateway / the UI's nginx; everything
behind it is in-VPC/account.

## Assets
- Ingested documents + their fossil layers (S3 silver/gold, pgvector).
- The prompt-fossilization cache + idempotency ledger (DynamoDB).
- Bedrock invocation budget (cost) and the managed-DB credentials (Secrets Manager).

## STRIDE

| # | Category | Threat | Mitigation (status) |
|---|----------|--------|---------------------|
| S | **Spoofing** | Unauthenticated callers hit `/mutate`, `/ingest` | Optional API-key auth (`FOSSILRAG_API_KEY`, constant-time compare), off only for local/demo; in front of API Gateway add a custom authorizer / WAF for prod. **(app: done; authorizer: documented)** |
| S | Spoofing | S3 spoofs SQS messages | Queue policy restricts `sqs:SendMessage` to the raw bucket ARN (`aws:SourceArn`), worker validates the event shape. **(done)** |
| T | **Tampering** | Forged/poisoned ingestion events | Content-addressed `doc_id`/`chunk_id` (sha256); the worker only reads from the raw bucket; idempotent upserts. **(done)** |
| T | Tampering | SQL injection via document/query input | All values bound via asyncpg `$N` placeholders; the only interpolated SQL identifier is the table name, sanitised by `_safe_ident()`. bandit B608 audited. **(done)** |
| T | Tampering | XSS / clickjacking in the UI | nginx CSP (`default-src 'self'`, fonts allowlisted), `X-Frame-Options: DENY`, `nosniff`; React escapes text by default (no `dangerouslySetInnerHTML`). **(done)** |
| R | **Repudiation** | No audit trail of who did what | Structured `key=value` logs + per-request correlation id (`X-Request-ID`) on every line; CloudTrail covers the AWS control plane. **(app: done)** |
| I | **Information disclosure** | DB creds / secrets in code or logs | DSN in Secrets Manager (not provisioned in IaC, injected at deploy); no secrets in the repo (gitleaks-scanned, allowlist only for fake demo values); logs are `event=` fields, not payloads. **(done)** |
| I | Info disclosure | Public S3 / unencrypted data | All buckets public-access-blocked + SSE-KMS; SQS SSE; DynamoDB encrypted at rest; TLS in transit (API Gateway / CloudFront-fronting optional). **(done)** |
| D | **Denial of service** | Burst traffic / cost blowout | SQS decouples + absorbs bursts; worker `maximum_concurrency` cap; API provisioned-concurrency autoscaling with a `max_capacity` ceiling; prompt-fossilization cache cuts repeat LLM spend; **add API Gateway throttling + WAF rate rules for prod (documented)**. |
| D | DoS | Poison message wedges the queue | Redrive to DLQ after `maxReceiveCount`; DLQ-not-empty CloudWatch alarm → SNS. **(done)** |
| E | **Elevation of privilege** | Over-broad IAM | Per-function least-privilege roles; worker scoped to its queue + silver prefix + one table; API Bedrock scoped to model/inference-profile ARNs (not `*`), DynamoDB to the cache table, AOSS to the collection ARN. **(done; tighten Bedrock to exact model ARNs in prod)** |

## Residual risks / explicitly deferred
- **No WAF / API Gateway throttle / network authorizer** in the IaC yet — the
  app-level API key is the current gate; a managed authorizer + WAF is the
  documented prod next step.
- **No request-body size limit** at the app layer (API Gateway's 10 MB payload
  cap applies); add an explicit limit if exposing untrusted upload.
- Dependency CVEs are scanned (pip-audit / bun audit, advisory) but third-party
  fixes are out of our control; pinned + lockfiled to bound drift.
