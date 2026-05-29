# FossilRAG 🦕

> Serverless, self-healing document enrichment & retrieval — the Dinosaur
> Whisperer's fossil-excavation engine.

FossilRAG ingests business documents (PPTX / PDF / text), extracts their
textual *DNA*, splits them into semantic **fossil layers**, embeds the content,
and exposes it through a FastAPI for search and LLM-powered mutation. It is
built **serverless-first** (S3 · Lambda · DynamoDB · API Gateway · Bedrock),
runs **end-to-end at $0 locally** (docker-compose + Postgres/pgvector + a
deterministic mock embedder), and is **one `terraform apply` from live AWS**.

This repo is built **walking-skeleton-first**: the full spine
(ingest → chunk → embed → store → retrieve) runs from PR0, and every later PR
*deepens one stage* while `main` stays demoable and each PR is independently
green.

---

## Quickstart (local, $0)

```bash
cd fossilrag
pip install -e .              # so host-run scripts (seed) can import fossilrag
make up                       # build + start postgres(pgvector) + api
python -m scripts.seed        # ingest the sample fossils
curl 'localhost:8000/excavate?q=late+cretaceous+apex+predator&k=3'
make down
```

Or prove the whole spine over HTTP in one shot:

```bash
make e2e                      # up -> ingest -> excavate -> assert -> down
```

> The dev box has no Docker, so the compose flow is **verified in CI** (the
> `compose-e2e` job actually `docker compose up`s the stack and runs
> `scripts/smoke.py`). Locally, `make test-unit` runs the unit suite.

---

## API (current)

| Method | Path        | Purpose |
|--------|-------------|---------|
| `GET`  | `/excavate` | Embed a query, return top-k nearest fossil chunks + geological-age metadata |
| `POST` | `/ingest`   | Run a document through the full spine and index its fossils |
| `POST` | `/mutate`   | Retrieve relevant fossils → grounded LLM summary/edit (pluggable mock/Bedrock/Anthropic) with Prompt Fossilization |
| `GET`  | `/timetravel` | A document's fossils at a chosen layer version + how it relates to the latest |
| `GET`  | `/diff`     | Changes between two fossil-layer versions of a document |
| `POST` | `/enrich`   | Extract structured markers (dates/metrics/error codes) → versioned enrichment record |
| `GET`  | `/markers`  | Retrieve a document layer's stored enrichment record |
| `POST` | `/chat`     | Multi-turn chat grounded in fossils (optional `source_id` scope) with geological-age citations |
| `POST` | `/slide/mutate` | Propose a slide edit + diff; optionally persist as a new fossil-layer version |
| `POST` | `/dataset`  | Build a fine-tuning JSONL dataset (chat/alpaca) from a fossil layer |
| `GET`  | `/healthz`  | Pool + store readiness |
| `GET`  | `/`         | Service info |

`/mutate` defaults to a deterministic mock LLM (`mock: true`) so the full
surface is callable at $0. For **real RAG**, flip to the OpenAI-compatible
provider — **OpenAI** (recommended; ~pennies for a demo) or **free Google
Gemini** (+ local embeddings) — for real semantic search *and* grounded
summaries; see `.env.example` + [`docs/demo.md`](docs/demo.md) (verify model
names are current). (Bedrock/Anthropic also supported.)
Optional API-key auth: set `FOSSILRAG_API_KEY` (off by default
for local/demo). Nearly every endpoint is surfaced in the **React UI** (all but
the read-only `GET /markers`) — `make up-ui` → http://localhost:5173.

---

## Architecture

```
            ┌──────────── ingest ───────────┐   ┌──── gold ────┐   ┌── vector ──┐
  upload →  │ extract text + provenance     │ → │ clean +      │ → │ embed +    │
 (S3 raw)   │ txt/md/pdf/pptx → silver JSON  │   │ semantic     │   │ idempotent │
   │        │ (S3-event Lambda)             │   │ chunk        │   │ upsert     │
   └─event─▶└───────────────────────────────┘   └──────────────┘   └─────┬──────┘
                                                                          │
                          ┌─────────────── FastAPI ────────────────┐      │
                  query → │ /excavate  top-k cosine search          │ ←────┘
                          │ /mutate    retrieve → LLM (Bedrock)     │
                          └─────────────────────────────────────────┘
```

- **Embeddings are pluggable** behind one interface: a deterministic **mock**
  (default, $0), a local **sentence-transformers** fallback (384-dim), and
  **Bedrock Titan v2** (1024-dim) as the cloud default. Indexes are keyed by
  `(model_id, dim)` and never cross-queried — vectors from different models are
  incomparable even at equal dimensions.
- **Vector store is behind one interface**: **pgvector** is the shipped backend
  (tested + demoed). **OpenSearch Serverless** is IaC-provisioned (cloud-native
  option), not yet bound behind the interface — the documented next step.
- See [`docs/architecture.md`](docs/architecture.md) and the decision record
  [`docs/adr/0001-foundational-decisions.md`](docs/adr/0001-foundational-decisions.md).

---

## Use cases & mutations

FossilRAG implements **all three** brief use cases as one composed system, and
**all seven** mutations:

- **Use cases:** Automated Enrichment Pipeline · Chat-Based Fossil Excavation ·
  PowerPoint Slide Mutator.
- **Mutations:** Time-Travel Query · Fossil Diff · Prompt Fossilization ·
  Self-Healing Idempotency · Auto-Scaling Lambda + DLQ · React Fossil UI ·
  Fine-Tuning Dataset Builder.

### Build roadmap

| PR | Status | Deepens |
|----|--------|---------|
| 0  | ✅ | **Walking skeleton** — ingest → chunk → mock-embed → pgvector → `/excavate` (+ mock `/mutate`) |
| 1  | ✅ | Ingestion: real PPTX/PDF/TXT/MD extraction, S3 raw→silver, S3-event Lambda |
| 2  | ✅ | Chunking: cleaning + token-aware semantic chunks w/ overlap + versioned gold (JSONL/Parquet) |
| 3  | ✅ | Embedding: pluggable local (sentence-transformers) + Bedrock Titan v2 + **Self-Healing Idempotency** (DynamoDB ledger) |
| 4  | ✅ | `/mutate`: pluggable LLM (mock/Bedrock Converse/Anthropic) + **Prompt Fossilization** (output cache) |
| 5  | ✅ | **Time-Travel Query** (`/timetravel`) + **Fossil Diff** (`/diff`) over versioned fossil layers |
| 6  | ✅ | **Automated Enrichment** (`/enrich`, `/markers`): extract dates/metrics/error-codes → structured DB |
| 7  | ✅ | **Chat Excavation** (`/chat`): multi-turn, source-scoped retrieval, geological-age citations |
| 8  | ✅ | **PPTX Slide Mutator** (`/slide/mutate`): edit suggestion + diff + version tracking (new fossil layer) |
| 9  | ✅ | **Fine-Tuning Dataset Builder** (`/dataset`): JSONL instruction/response pairs from gold fossils |
| 10 | ✅ | **Auto-Scaling Lambda + DLQ**: SQS worker (partial-batch-failure, retry/backoff, idempotent) + DLQ handler |
| 11 | ✅ | **Terraform IaC** — S3/SQS/DynamoDB/Lambda/API-GW/AOSS, autoscaling, DLQ redrive, lifecycle; `validate`-clean, one-`apply` live |
| 12 | ✅ | **Full compose stack** — LocalStack ingestion loop (`aws` profile) + worker poller; profile-gated, moto-tested |
| 13 | ✅ | **React Fossil UI** — 8-tab Excavation Console (Bun/Vite/React 19/Biome/Vitest), OpenAPI-typed, nginx + `/api` proxy |
| 14 | ✅ | **Observability + security** — EMF metrics + correlation ids + CloudWatch alarms/dashboard; optional API-key auth, CSP, scoped IAM, bandit/gitleaks/pip-audit CI |
| 15 | ✅ | **Docs + demo polish** — architecture diagram, runbook, cost, threat model, demo script, load test, rubric self-check |

---

## Development

```bash
make install     # pip install -e ".[dev]"
make lint        # ruff check + format check
make test-unit   # unit tests (no DB)
make test        # full suite (needs DATABASE_URL; runs in CI)
make up-ui       # postgres + api + React UI (http://localhost:5173)
make loadtest    # latency percentiles + throughput on /excavate
```

---

## Docs

| Doc | What |
|-----|------|
| [`docs/architecture.md`](docs/architecture.md) | Topology diagram, medallion data flow, interfaces |
| [`docs/rubric.md`](docs/rubric.md) | Deliverable & rubric self-check (components · 3 use cases · 7 mutations) |
| [`docs/demo.md`](docs/demo.md) | ~6-min demo walkthrough (all use cases + UI + the AWS loop) |
| [`docs/runbook.md`](docs/runbook.md) | Deploy / operate / DLQ redrive / secret rotation |
| [`docs/observability.md`](docs/observability.md) | Logs · EMF metrics · X-Ray · alarms + dashboard |
| [`docs/threat-model.md`](docs/threat-model.md) | STRIDE threat model + mitigations |
| [`docs/cost-notes.md`](docs/cost-notes.md) | Cost posture + optimisations |
| [`docs/adr/0001-foundational-decisions.md`](docs/adr/0001-foundational-decisions.md) | Dated decision record |
| [`infra/README.md`](infra/README.md) · [`docker/README.md`](docker/README.md) · [`ui/README.md`](ui/README.md) | Component-level guides |
