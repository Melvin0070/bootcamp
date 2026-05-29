# FossilRAG — Deliverable & Rubric Self-Check

Maps every brief requirement to where it lives and how it's verified. Shipped
across 16 focused PRs (PR0–PR15), each independently green on `main`.

## Components

| # | Component | Where | Verified |
|---|-----------|-------|----------|
| 1 | Serverless ingestion (S3, Lambda, PPTX/PDF/text→JSON) | `ingest/` (`extract.py`, `handler.py`, `storage.py`), `infra/{s3,lambda,sqs}.tf` | unit + moto; terraform validate |
| 2 | Text cleaning & semantic chunking + gold layer | `chunking/` (`text.py`, `chunker.py`, `gold.py`) | unit (idempotent, token-aware) |
| 3 | Embedding + vector index + idempotency | `embedding/` (mock/local/Bedrock), `vectorstore/pgvector.py`, `idempotency.py` | unit + integration (pgvector HNSW) |
| 4 | Retrieval API (FastAPI) `/excavate` + `/mutate` (+ prompt caching) | `api/app.py`, `llm/` (`cache.py` fossilization) | unit + integration + compose-e2e |
| 5 | IaC & cost optimisation (auto-scaling, DLQ, lifecycle) | `infra/*.tf` (S3/SQS/DynamoDB/Lambda/API-GW/AOSS/monitoring) | `terraform fmt -check` + `validate` in CI |
| 6 | Containerisation & local orchestration (Docker, compose, LocalStack) | `docker/`, `ui/Dockerfile`, `docker-compose.yml` (`aws`/`ui` profiles) | `docker compose config` (3 profiles) + compose-e2e |

## Use cases — **all three** delivered

| Use case | Endpoint(s) / UI | Where |
|----------|------------------|-------|
| Chat-Based Fossil Excavation | `/chat` + Chat tab | `api/app.py`, `llm/`, `ui/src/panels/ChatPanel.tsx` |
| PowerPoint Slide Mutator (edit + version tracking) | `/slide/mutate` + Slide tab | `api/app.py`, `ui/.../SlideMutatePanel.tsx` |
| Automated Enrichment Pipeline (markers → structured) | `/enrich` + `/markers` + Enrich tab | `enrichment/markers.py`, `ui/.../EnrichPanel.tsx` |

## Mutations — **all seven** delivered

| Mutation | Where |
|----------|-------|
| Time-Travel Query | `/timetravel`, `timetravel.py`, Fossil Layers tab |
| Fossil Diff | `/diff`, `timetravel.py` (unified diff) |
| Prompt Fossilization (output cache) | `llm/cache.py` (memory/local/DynamoDB), `/mutate`+`/chat` `cached` flag |
| Self-Healing Idempotency (skip indexed chunks) | `idempotency.py` (Null/Local/DynamoDB), worker skip path |
| Auto-Scaling Lambda + DLQ | `worker/` (partial-batch + retry), `infra/{sqs,lambda}.tf` (redrive + appautoscaling) |
| React Fossil UI | `ui/` (8-tab Excavation Console) |
| Fine-Tuning Dataset Builder | `dataset/builder.py`, `/dataset` (JSONL chat/alpaca) |

## Expected deliverables

| Deliverable | Status |
|-------------|--------|
| Text cleaning + chunking module (Lambda-deployable) | ✅ `chunking/` |
| Embedding script + index with idempotency | ✅ `embedding/` + `vectorstore/` + `idempotency.py` |
| FastAPI `/excavate` + `/mutate` (+ 8 more endpoints) | ✅ `api/app.py` |
| CloudFormation/Terraform templates | ✅ `infra/*.tf` (Terraform, AWS provider ~>6) |
| Dockerfiles + docker-compose | ✅ `docker/`, `ui/Dockerfile`, `docker-compose.yml` |
| Architecture diagram, runbook, cost notes | ✅ `docs/{architecture,runbook,cost-notes}.md` |
| (extra) Observability, threat model, demo, load test, ADR | ✅ `docs/{observability,threat-model,demo,rubric}.md`, `docs/adr/`, `scripts/loadtest.py` |

## Key principles

- **Serverless-first** — S3/Lambda/SQS/DynamoDB/API-GW/Bedrock/AOSS; Mangum ASGI→Lambda.
- **Idempotency everywhere** — content-addressed `doc_id`/`chunk_id`; idempotency ledger; idempotent upserts + IaC provisioning.
- **Modular + logging + error handling** — pluggable Embedder/VectorStore/LLM/Cache/Idempotency behind interfaces; structured `event=` logs + EMF metrics + correlation ids.
- **Incremental, documented PRs** — 16 PRs, each green; ADR records the foundational decisions.

## Verification posture (honesty)

- **CI gates** (`fossilrag-ci.yml`): ruff · pytest matrix (3.12/3.13) on real pgvector + 85% coverage · `docker compose config` (3 profiles) + compose-e2e smoke · `bun` (Biome/typecheck/Vitest/build) · `terraform fmt+validate` · `bandit` (pip-audit/gitleaks/bun-audit advisory).
- **$0-verified** everywhere: pgvector + mock embedder/LLM + moto + LocalStack-profile config.
- **Plan-validated, never claimed "deployed"**: Bedrock (behind the Embedder/LLM interfaces) + OpenSearch Serverless (IaC-provisioned, not yet bound behind the VectorStore interface) — no $0 emulation, IaC-validated. The `aws`/`ui` compose profiles + live AWS run are manual (documented), not CI-verified end-to-end.
