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
| `POST` | `/mutate`   | Retrieve relevant fossils and return a grounded summary/edit (**mock LLM in PR0**; real Bedrock Converse + Prompt Fossilization in PR4) |
| `GET`  | `/healthz`  | Pool + store readiness |
| `GET`  | `/`         | Service info |

`/mutate` currently returns a deterministic mock summary (`mock: true`) so the
full surface is callable at $0. The further mutation endpoints (`/timetravel`,
`/diff`, `/dataset`) arrive in later PRs.

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
- **Vector store is pluggable**: **pgvector** (primary, tested + demoed),
  **FAISS** (unit alternate), **OpenSearch Serverless** (cloud-native, IaC-validated).
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
| 5  | ⬜ | Time-Travel Query + Fossil Diff |
| 6  | ⬜ | Automated Enrichment (markers: dates/metrics/error codes) |
| 7  | ⬜ | Chat Excavation |
| 8  | ⬜ | PPTX Slide Mutator |
| 9  | ⬜ | Fine-Tuning Dataset Builder |
| 10 | ⬜ | Auto-Scaling Lambda + DLQ |
| 11 | ⬜ | Terraform IaC (live-deploy-ready) |
| 12 | ⬜ | Full compose stack (LocalStack + workers + UI) |
| 13 | ⬜ | React Fossil UI |
| 14 | ⬜ | Observability + security hardening |
| 15 | ⬜ | Docs + demo polish |

---

## Development

```bash
make install     # pip install -e ".[dev]"
make lint        # ruff check + format check
make test-unit   # unit tests (no DB)
make test        # full suite (needs DATABASE_URL; runs in CI)
```
