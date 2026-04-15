# Bootcamp: FossilRAG & Certification

## Context

3-week bootcamp to strengthen system design, scalability, AWS infrastructure, and agentic AI skills.
Final outcome: build and demo **FossilRAG** — a serverless document enrichment and retrieval system.

---

## Weekly Schedule (54 hours total, Mon–Sat, 3h/day)

| Week | Focus |
|------|-------|
| Week 1 + first half of Week 2 | Certification courses (21h) + daily case study + broken repo (12 total) |
| Week 2 (Day 5) → Week 3 | FossilRAG project (21h) |

---

## Certification Courses (must complete by end of Week 2)

1. **Agentic AI** — 7 hours
2. **AWS Technical Essentials** — 7 hours
3. **System Design Foundations** — 7 hours

Deliverable: submit certificate link or screenshot per course.

---

## Daily Case Study + Broken Repo (Weeks 1–2)

- One case study + one broken repo per day
- Identify issues, fix, raise a PR
- Add 2–5 min video walkthrough (before/after)
- Total: 12 case studies across Weeks 1–2

---

## Main Project: FossilRAG

**Domain:** Dinosaur Whisperer — serverless document enrichment and retrieval.

**Chosen Use Case:** TBD (options below)

**Chosen Mutations:** TBD (at least two)

### Use Cases (choose one)

- **PowerPoint Slide Mutator** — editing suggestions + version tracking
- **Chat-Based Fossil Excavation** — NL questions over uploaded docs + "geological age" metadata
- **Automated Enrichment Pipeline** — ingest reports/logs, extract key markers, enrich structured DB

### Mutations (choose at least two)

- Time-Travel Query — query a specific fossil layer, compare with latest
- Fossil Diff — show changes between two document versions
- Prompt Fossilization — cache successful prompts/outputs for instant reuse
- Self-Healing Idempotency — skip already-indexed chunks via DynamoDB
- Auto-Scaling Lambda with DLQ — burst traffic + failed event handling
- React Fossil UI — minimal frontend for excavation/mutation
- Fine-Tuning Dataset Builder — generate JSONL instruction/response pairs from gold chunks

### Components

| # | Component | Key tech |
|---|-----------|----------|
| 1 | Serverless Document Ingestion | S3, Lambda, PPTX/PDF/text → JSON/Parquet |
| 2 | Text Cleaning & Semantic Chunking | Python Lambda, paragraph/sentence splits, gold layer |
| 3 | Embedding Generation & Vector Indexing | sentence-transformers or OpenAI, FAISS/Chroma, idempotency |
| 4 | Retrieval API (FastAPI) | `/excavate` (top-k search), `/mutate` (LLM summary/edit), prompt caching |
| 5 | Infrastructure as Code & Cost Optimisation | CloudFormation/Terraform, auto-scaling, DLQ, lifecycle policies |
| 6 | Containerisation & Local Orchestration | Docker, docker-compose, LocalStack for S3 simulation |

### Expected Deliverables

- Text cleaning + chunking module (Python, Lambda-deployable)
- Embedding script + FAISS/Chroma index with idempotency
- FastAPI `/excavate` and `/mutate` endpoints
- CloudFormation/Terraform templates
- Dockerfiles + docker-compose
- Architecture diagram, runbook, cost notes

---

## Repo Structure (target)

```
bootcamp/
├── CLAUDE.md
├── README.md
├── courses/           # notes, screenshots, certificate links
├── case-studies/      # daily case study writeups
├── broken-repos/      # daily broken repo PRs
└── fossilrag/         # main project (monorepo)
    ├── ingestion/     # Component 1: Lambda + S3 ingestion
    ├── chunking/      # Component 2: text cleaning + chunking
    ├── embedding/     # Component 3: embedding + vector index
    ├── api/           # Component 4: FastAPI
    ├── infra/         # Component 5: IaC (CloudFormation/Terraform)
    ├── docker/        # Component 6: Dockerfiles + docker-compose
    └── docs/          # architecture diagram, runbook, cost notes
```

---

## Key Principles

- Serverless-first architecture
- Modular functions, logging, error handling throughout
- Idempotency everywhere (skip already-processed data)
- Work incrementally — commit often, document design decisions
- Keep PRs focused with clear descriptions
