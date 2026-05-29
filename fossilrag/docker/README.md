# Containerisation & Local Orchestration (Component 6)

Two layers, selected by Docker Compose profile.

## 1. The lean spine — `docker compose up` (default)

Postgres+pgvector and the API. No AWS, no token. This is the path the
`compose-e2e` CI job builds and smoke-tests (`scripts.smoke`: ingest → excavate
over real HTTP), so it stays self-contained and always green.

```bash
make up            # docker compose up -d --build
make smoke         # POST /ingest -> GET /excavate
make down
```

| Service | Image | Role |
|---------|-------|------|
| `postgres` | `pgvector/pgvector:pg16` | the live vector backend (HNSW + `<=>`) |
| `api` | `docker/api.Dockerfile` | FastAPI; mock embedder, `$0` |

## 2. The emulated serverless stack — `--profile aws`

Adds LocalStack (S3 + SQS + DynamoDB) and the decoupled ingestion loop, so the
**same topology Terraform deploys** (`../infra/`) runs locally at `$0`:

```
upload to raw S3  →  S3 ObjectCreated  →  SQS  →  worker  →  silver S3
                                              └─ DynamoDB idempotency ledger
```

| Service | Role |
|---------|------|
| `localstack` | S3 + SQS + DynamoDB on `:4566` |
| `bootstrap` (one-shot) | `scripts.localstack_init` → provisions buckets, ingest queue + DLQ (redrive), DynamoDB tables, raw→SQS notification (`fossilrag.aws.bootstrap.provision`) |
| `worker` | `scripts.worker` → long-polls the ingest queue and feeds the **same** `sqs_handler` the cloud runs (`fossilrag.worker.poller`) |

```bash
export LOCALSTACK_AUTH_TOKEN=...   # see below
make up-aws        # docker compose --profile aws up -d --build
make demo          # S3 -> SQS -> worker -> silver, then idempotent re-drop
make smoke         # the retrieval half, over pgvector
make down
```

`make demo` is the headline: it uploads documents to the raw bucket, waits for
the worker to land them in silver, then **re-uploads the same objects** to show
self-healing idempotency — the redelivered S3 events are no-ops (no new silver
writes), and the DynamoDB ledger reports them as already processed.

## LocalStack auth token (required)

Since **LocalStack 2026.03** the unified image needs an account token — even
the free Hobby tier (the old community-image functionality). It's free for
students (GitHub-verified), OSS, and non-profits; grab one in ~90s at
[app.localstack.cloud](https://app.localstack.cloud). Set
`LOCALSTACK_AUTH_TOKEN` before `make up-aws`. Pin `LOCALSTACK_IMAGE` to a
specific version for reproducibility.

## Honesty / verification posture

Same posture as the Terraform stack — claim only what's verified:

- **The lean spine is CI-verified end-to-end** (built + HTTP-smoked every PR).
- **The `aws` profile is NOT run in CI.** LocalStack's token requirement makes
  it unsuitable for unattended CI, so instead:
  - the orchestration **logic** — `provision()`, the SQS **poller**, and the
    worker's S3-event parsing — is unit-tested against **moto** (in-process,
    `$0`, no token) in the `test` job;
  - the compose file is **structure-validated** in CI (`docker compose config`
    for both the default and `aws` profiles);
  - the full LocalStack loop is **manually runnable** via `make up-aws` +
    `make demo` with a free token.
- **The worker lands documents in silver.** Embedding + indexing into pgvector
  is the API's `/ingest` path (the retrieval half); a silver→embed→index
  consumer is a documented future step, not faked here.
