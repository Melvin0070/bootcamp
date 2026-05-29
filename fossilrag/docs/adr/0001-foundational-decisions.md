# ADR-0001: Foundational architecture decisions

- **Status:** Accepted
- **Date:** 2026-05-29
- **Context:** Kickoff of the FossilRAG main project. All versions/facts below
  were verified against current (May 2026) primary sources, cited inline.

## Decision summary

| # | Decision | Why |
|---|----------|-----|
| 1 | **Hybrid AWS target**: build + test at $0 (LocalStack/moto + mock + pgvector); ship a turnkey, `plan`-validated Terraform stack | User needs it live-capable but cannot incur dev cost |
| 2 | **Pluggable AI providers, mock by default**; AWS Bedrock as the cloud default | $0 CI/tests with no keys; serverless-native in cloud |
| 3 | **pgvector is the only shipped vector store**; OpenSearch Serverless is IaC-provisioned but not yet bound (FAISS a not-yet-built candidate) | One $0-verifiable backend; don't pay 3× test surface for one demo |
| 4 | **Walking-skeleton-first** delivery; one stage deepened per PR | `main` always demoable; autonomous merges stay safe |
| 5 | **Indexes keyed by `(model_id, dim)`**, never cross-queried | Vectors from different models are incomparable even at equal dims |

---

## 1. AWS target: hybrid ($0 dev, live-ready)

LocalStack's model changed materially since early 2024: the Community/Pro split
was consolidated (2026.03.0) into one image gated by an **auth token**; the free
tier is now **"Hobby"** ($0, **non-commercial**), and the no-token grace period
(`LOCALSTACK_ACKNOWLEDGE_ACCOUNT_REQUIREMENT=1`) **ended 2026-04-06**.
Crucially: **Bedrock is Ultimate-tier only** (and needs Ollama), and
**OpenSearch Serverless is unsupported on every tier**.

**Consequences:**
- Use **`moto`** (in-process, free, no token) for AWS-service *tests* in CI.
- Use **LocalStack** only for the human-run compose demo (documented free
  token), for the services it does support ($0 Hobby): S3, DynamoDB, SQS, SNS,
  Lambda, IAM/STS, Secrets Manager, SSM, API Gateway, EventBridge, Step
  Functions, CloudWatch Logs.
- Bedrock + AOSS are **mock-backed + `terraform plan`/`validate`-verified**,
  never claimed as "ran live." A real billed `terraform apply` is a one-command
  step the user runs with their creds.

Sources: LocalStack licensing & release notes 2026.03.0/2026.05.0; service
pages for Bedrock and OpenSearch.

## 2. AI providers (pluggable; Bedrock cloud default)

- **LLM (`/mutate`, PR4):** AWS Bedrock via the **Converse API** (`converse` /
  `converse_stream`), using a **cross-region inference profile ID** as
  `modelId` (e.g. `us.anthropic.claude-sonnet-4-6`) — the bare foundation-model
  ID fails on-demand in most regions. **Prompt caching** via `cachePoint`
  blocks powers the Prompt Fossilization mutation (min 1024 cached tokens for
  Sonnet 4.6; 4096 for Opus/Haiku 4.5). Mock LLM is the CI/test default.
- **Embeddings (PR3):** **Amazon Titan Text Embeddings V2**
  (`amazon.titan-embed-text-v2:0`) at **1024 dims, `normalize=true`** as the
  cloud default; **`all-MiniLM-L6-v2` (384 dims)** as the local
  sentence-transformers fallback; a deterministic **mock** (384) as the $0
  default. sentence-transformers (~2GB torch) cannot ship in a Lambda zip — the
  architectural reason Bedrock is the cloud default and the local model is a
  dev-only fallback.

Sources: Bedrock Converse, prompt-caching, inference-profiles, and Titan
embeddings user-guide pages; sentence-transformers 5.5.x docs.

## 3. Vector store: pgvector primary

- **pgvector 0.8.x** (upstream 0.8.2; RDS/Aurora cap at 0.8.0 — don't rely on
  newer fixes in cloud). Type `vector(dim)`; **HNSW** index with
  `vector_cosine_ops`, queried with `<=>`. Defaults `m=16, ef_construction=64`;
  recall via `hnsw.ef_search` (≥ top-k). Register the pgvector asyncpg codec on
  pool `init` so numpy float32 arrays bind directly. Indexed `vector` caps at
  2000 dims (1024 is fine; `halfvec` only needed beyond that).
- **FAISS** — a candidate lightweight in-process backend; **not yet implemented**
  (pgvector is the only shipped/tested store).
- **OpenSearch Serverless** — cloud-native option: `VECTORSEARCH` collection,
  Faiss HNSW, three policies (encryption object + network/data arrays) with
  `depends_on`; **no custom doc IDs** on vector collections. AWS provider
  `~> 6.0`. Not emulable at $0 → mock-client unit tests + `terraform plan`.

Sources: pgvector README/CHANGELOG + RDS/Aurora announcements; AOSS vector-search
developer guide + Terraform `opensearchserverless_*` resources.

## 4. Delivery: walking-skeleton-first

PR0 ships the full spine at trivial fidelity (txt ingest, paragraph chunks,
deterministic mock embeddings, pgvector, `/excavate`) with a CI-verified
`docker compose` e2e. Each later PR deepens one stage. Every PR is independently
green (required: merges are autonomous), landed via `/ship` → Copilot review →
iterate → merge.

## 5. Provenance: `(model_id, dim)` keying

Every vector row records its `model_id` and `embed_dim`. Switching embedding
provider changes the vector space and **requires a full re-embed into a new
fossil layer** — which is exactly the Time-Travel / versioning model, so the
constraint and the feature reinforce each other.

## Tooling versions pinned (May 2026)

FastAPI 0.136 · Pydantic 2.13 / pydantic-settings 2.14 · asyncpg 0.30 ·
pgvector(py) 0.3.6+ · numpy 2.1 · Terraform core ≥1.9 + AWS provider ~>6.0
(6.47) · LocalStack 2026.05 · boto3 1.43 · sentence-transformers 5.5 ·
pypdf 6.x · python-pptx 1.0.2 (stale — verify on Py3.14 in PR1).
