# FossilRAG — Cost Notes

How the design keeps spend low, and where the real costs land in a live deploy.
Grows per PR; currently covers ingestion (PR1) + the retrieval/embedding path.

## Dev / CI: $0 by design

- **Mock embedder + pgvector + moto** mean the whole pipeline runs and is
  tested with **no AWS spend and no keys**. Bedrock and OpenSearch Serverless —
  the things that *do* cost money — sit behind interfaces and are never
  invoked at $0 (mock-backed + `terraform plan`-validated).
- CI uses a `pgvector/pgvector` service container (free GitHub-hosted minutes)
  and `moto` for S3/DynamoDB/SQS — no LocalStack token, no cloud calls.

## Live deploy — where cost accrues

| Service | Driver | Mitigation in the design |
|---------|--------|--------------------------|
| **S3** | raw + silver storage, PUT/GET requests | content-addressed keys avoid duplicate silver; **lifecycle policies** (PR11) transition/expire raw + intermediate layers |
| **Lambda** (ingestion) | invocations × duration × memory | pure-Python `pypdf` keeps the package small; arm64 + right-sized memory (PR11); DLQ stops infinite retries on poison objects (PR10) |
| **Bedrock** (PR4) | input/output tokens; embeddings per RPM | **Prompt Fossilization** (prompt caching) cuts repeat input-token cost; Titan v2 at 1024 dims (or 512 ≈ 99% recall) trades index size for cost |
| **DynamoDB** (PR3) | idempotency ledger + prompt cache | `PAY_PER_REQUEST` + TTL; tiny per-item size |
| **Vector store** | RDS/Aurora pgvector *or* OpenSearch Serverless OCUs | pgvector on a small instance for modest scale; AOSS only when scale warrants (`standby_replicas=DISABLED` in dev) |

## Tagging & visibility

- Terraform sets provider-level **`default_tags`** (`Project`, `Environment`,
  `ManagedBy`) so Cost Explorer can attribute spend (PR11).
- The biggest single lever is **idempotency**: never re-embed an already-indexed
  chunk (content-addressed `chunk_id` + the PR3 DynamoDB ledger), so steady-state
  embedding cost trends to ~$0 once a corpus is indexed.
