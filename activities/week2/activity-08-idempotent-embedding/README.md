# Activity 8: Make the Embedding Pipeline Idempotent

**Week:** 2 | **Day:** 8 | **Course alignment:** System Design Foundations

## Problem Statement

The embedding generation pipeline re-embeds **all chunks on every run**,
wasting compute and creating duplicate vectors in the FAISS/Chroma index.

## What to Fix

- [x] Track processed chunks via a **DynamoDB table** *or* **S3 manifest**
      *or* a local manifest, behind a single `IdempotencyStore` Protocol
- [x] Skip chunks already in the manifest (zero API calls on a clean re-run)
- [x] On successful embedding, atomically commit the chunk_id → embedding URI
- [x] Handle partial failures: a chunk that failed mid-run is retried next run
- [x] Bonus: content-addressed `chunk_id = sha256(text + metadata)` so the
      same paragraph in two documents dedupes to one vector

## Acceptance Criteria

- Re-running the pipeline does not re-embed already-processed chunks ✅
  (verified: `embedder.calls == []` on second run)
- A new chunk is embedded exactly once ✅
- Failed chunks are retried on the next run ✅

## What Was Fixed

| # | Anti-pattern (broken) | Fix applied | Impact |
|---|---|---|---|
| 1 | Sequential int chunk ids | `chunk_id = sha256(text + metadata)` (content-addressed) | Re-runs are deterministic; the same paragraph in two documents dedupes to one vector |
| 2 | No idempotency check | `IdempotencyStore` Protocol with `is_processed` / `claim` / `mark_processed` / `release` | Re-runs cost ~$0; only new chunks hit the API |
| 3 | One implementation only | Three backends: `DynamoDBStore`, `S3ManifestStore`, `LocalManifestStore` | Same orchestrator runs in dev (local), batch (S3), and multi-worker prod (DDB) |
| 4 | No atomic claims | DynamoDB `ConditionExpression="attribute_not_exists(chunk_id) OR ttl < :now"` | Multi-worker pipelines never embed the same chunk twice |
| 5 | No partial-failure recovery | `release()` on any embed/sink failure; DynamoDB TTL auto-reclaims dead PENDING records | Crash mid-run leaves no orphan claims; next run picks up clean |
| 6 | One API call per chunk | Batched calls (default 64/batch) with exponential-backoff retry on `RateLimitError` | 50× fewer API calls; survives transient rate-limit blips |
| 7 | Hardcoded paths | `os.environ.get(...)` for paths, model, batch size, backend | Same code in dev / batch / prod |
| 8 | `print()` logging | `logging.getLogger("embedding_pipeline")` + `event=key=value` records | CloudWatch Insights / Datadog parse without grok |
| 9 | Non-atomic sink writes | tmp + `os.replace` | A crash mid-write never leaves a partial `.npy` |

## Cost Impact

text-embedding-3-small @ $0.02/1M tokens, 50 k chunks × ~150 tokens:

| Scenario | Broken | Fixed |
|---|---|---|
| First run | ~$0.15 | ~$0.15 |
| Re-run, 0 changes | ~$0.15 | **~$0** |
| Re-run, 100 new chunks | ~$0.15 | **~$0.0003** |
| Daily run × 365 days, 0 changes | **~$55/year** | **~$0/year** |

## How to Run Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Expected: **31 tests passing** — 19 source-file analysis assertions plus
12 behaviour tests covering re-run skipping, partial-failure recovery,
content-id determinism, dedup, concurrent-claim atomicity, TTL reclaim,
and the env-var backend factory.

The behaviour tests use `LocalManifestStore` + `LocalSink` + a deterministic
`StubEmbedder`, so they run in any CI environment without AWS, OpenAI, or a
network connection.

## How to Run the Pipeline

```bash
cp .env.example .env
# choose one backend:
#   IDEMPOTENCY_BACKEND=local     (manifest.json on disk)
#   IDEMPOTENCY_BACKEND=s3        (one JSON object in an S3 bucket)
#   IDEMPOTENCY_BACKEND=dynamodb  (production, atomic, multi-worker)
pip install -r requirements.txt
python embedding_pipeline.py
```

## DynamoDB Table Schema

For `IDEMPOTENCY_BACKEND=dynamodb`:

```
TableName: fossilrag-chunk-registry
PartitionKey: chunk_id (S)
TTL: ttl (N)            ← enable TTL on this attribute
BillingMode: ON_DEMAND  ← traffic is bursty (one batch run per day)
```

CloudFormation snippet (paste into Activity 4's IaC):

```yaml
ChunkRegistryTable:
  Type: AWS::DynamoDB::Table
  Properties:
    TableName: fossilrag-chunk-registry
    AttributeDefinitions:
      - AttributeName: chunk_id
        AttributeType: S
    KeySchema:
      - AttributeName: chunk_id
        KeyType: HASH
    TimeToLiveSpecification:
      AttributeName: ttl
      Enabled: true
    BillingMode: PAY_PER_REQUEST
```

## Layout

```
activity-08-idempotent-embedding/
├── broken/
│   └── embedding_pipeline.py    # "before" — anti-patterns intact
├── chunk.py                     # content-addressed chunk_id = sha256(...)
├── idempotency.py               # IdempotencyStore Protocol + 3 backends
├── sink.py                      # LocalSink + S3Sink (atomic writes)
├── embedding_pipeline.py        # orchestrator: claim → embed → commit
├── tests/test_pipeline.py       # source-file + behaviour tests
├── docs/architecture.md         # state machine, trade-offs, edge cases
├── .env.example                 # documents every env var
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

## PR Checklist

- [x] Anti-patterns preserved in `broken/embedding_pipeline.py`
- [x] `.env.example` documents every environment variable
- [x] 22 pytest assertions cover anti-patterns, fixes, and behaviour
- [x] `docs/architecture.md` — state machine, before/after diagrams, backend
      trade-offs, edge cases, cost model, rollback plan
- [ ] 2–5 min video walkthrough (before/after) — to add

## Notes

**Why content-addressed ids over UUIDs at ingest time:** UUIDs would couple
the chunk identity to ingest time, so re-ingesting the same document would
produce a fresh set of ids and the manifest wouldn't help. Hashing the
content makes ingestion replay-safe and gives us cross-document dedup for
free.

**Why claim-then-embed instead of embed-then-claim:** the embedding API call
is the expensive step. A cheap claim before the API call detects races
*before* paying for the embedding. The downside is that a crashed run leaves
PENDING records — but DynamoDB TTL (or the local-backend staleness check)
auto-reclaims them, so it's a 0-ops trade-off.

**Why three backends:** dev iteration needs a manifest you can `cat` and
delete. Single-writer batch jobs don't need DDB's atomic-claim guarantee
and an S3 JSON manifest is cheaper. Multi-worker production needs DDB
because S3 last-write-wins would silently corrupt the manifest. The
`IdempotencyStore` Protocol means the orchestrator code is identical across
all three.

**Why SHA-256 over xxh3 / SHA-1:** xxh3 is faster but not collision-resistant
(it's a non-cryptographic hash); SHA-1 is broken for adversarial inputs.
SHA-256 is fast enough (~500 MB/s on a modern CPU) to never be the
bottleneck, and zero practical collision risk across realistic corpus sizes.

**The HASHED_METADATA_FIELDS list is a hard contract.** Adding or removing
a field changes every existing id, so the manifest from a previous run
becomes useless. Treat it like a schema migration — version the DynamoDB
table name (`chunk-registry-v2`) when you change it.
