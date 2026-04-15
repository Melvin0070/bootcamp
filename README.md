# Bootcamp — FossilRAG

3-week engineering bootcamp. Goal: build and demo **FossilRAG**, a serverless document enrichment and retrieval system, while completing 3 certification courses and 12 hands-on fix activities.

---

## Repo Structure

```
bootcamp/
├── courses/                    # Course notes + certificate links
│   ├── agentic-ai/
│   ├── aws-essentials/
│   ├── system-design/
│   └── claude-code-in-action/
├── activities/                 # 12 broken-repo fix activities
│   ├── week1/
│   │   ├── activity-01-lambda-pipeline/
│   │   ├── activity-02-llm-agent/
│   │   ├── activity-03-spark-dedup/
│   │   ├── activity-04-cloudformation-cost/
│   │   ├── activity-05-airflow-dag/
│   │   └── activity-06-scala-etl/
│   └── week2/
│       ├── activity-07-vector-search/
│       ├── activity-08-idempotent-embedding/
│       ├── activity-09-credentials/
│       ├── activity-10-cicd/
│       ├── activity-11-observability/
│       └── activity-12-fastapi-search/
└── fossilrag/                  # Main project (starts Week 2, Day 5)
    ├── ingestion/
    ├── chunking/
    ├── embedding/
    ├── api/
    ├── infra/
    ├── docker/
    └── docs/
```

---

## Weekly Schedule

| Week | Days | Focus |
|------|------|-------|
| 1 | Days 1–6 | Agentic AI course + Activities 1–6 |
| 2 (first half) | Days 7–12 | AWS + System Design courses + Activities 7–12 |
| 2 (Day 13) → Week 3 | Days 13–18 | FossilRAG project |

Each activity lives in `activities/weekN/activity-NN-*/`:
- `README.md` — problem statement, what to fix, acceptance criteria
- `broken/` — the broken code to fix
- Fix goes in the same folder; raise a PR per activity

---

## Certification Courses

| Course | Hours | Status |
|--------|-------|--------|
| Agentic AI | 7h | |
| AWS Technical Essentials | 7h | |
| System Design Foundations | 7h | |
| Claude Code in Action | — | |

---

## FossilRAG

Use case and mutations TBD — decided by Day 13. See `fossilrag/` once project starts.
