# Activity 8 — Idempotent Embedding Pipeline

## Problem Diagnosis

The original pipeline re-embedded every chunk on every run. Three failure
modes followed:

1. **Cost.** A 50 k-chunk corpus at text-embedding-3-small (~150 tok/chunk
   @ $0.02/M tokens) costs ~$0.15 per run. Running it daily is ~$55/year,
   99.7 % of which is wasted on already-embedded chunks.
2. **Duplicate vectors.** Chunk ids were sequential ints assigned at
   write-time, so a re-run wrote `0.npy`, `1.npy`, ... over the previous
   set — but if the FAISS index was built from the *full* directory, the
   same content ended up in the index multiple times under different ids,
   silently degrading retrieval quality.
3. **No partial-failure recovery.** A crash mid-batch left the pipeline
   with no record of what got done. The next run started over, doubling
   the cost on the unfinished portion of the corpus.

| Anti-pattern | Symptom | Root cause |
|---|---|---|
| Sequential int ids | Duplicate vectors after re-runs | Id assigned at write-time, not derived from content |
| No idempotency check | API cost grows linearly with re-runs | No registry of completed work |
| No batching | Rate-limited at ~3 k chunks (RPM cap) | One synchronous call per chunk |
| `print()` logging | Failure post-mortem is impossible | No structured fields |
| Hardcoded paths | Same code can't run dev/staging/prod | No env-var indirection |
| No retry on 429 | Pipeline dies on a transient rate-limit blip | `openai.RateLimitError` propagates uncaught |

---

## Architecture: Before vs After

### Before (broken)

```
chunks.jsonl
    │
    ▼
for i, chunk in enumerate(chunks):
    embedding = openai.embeddings.create(input=chunk["text"])  ← 1 API call/chunk
    np.save(f"{OUTPUT_DIR}/{i}.npy", embedding)                ← sequential id
                                                                  re-run overwrites
                                                                  but FAISS keeps old
```

### After (fixed)

```
chunks.jsonl ──▶ for chunk in chunks:
                     chunk_id = sha256(text + metadata)         ← content-addressed
                     │
                     ├─ store.is_processed(id)? ───yes──▶ skip
                     │                                          (skipped_processed++)
                     ▼
                     store.claim(id)? ──no (locked)─────▶ skip  (skipped_locked++)
                     │ yes
                     ▼
                     pending.append((id, chunk))
                     │
                     │ if len(pending) >= BATCH_SIZE:
                     ▼
                 ┌──────────────────────────────────────────────┐
                 │ embed_fn(texts)  (one batched API call)     │
                 │   on RateLimitError: exponential backoff    │
                 │   on persistent failure: store.release(id)  │
                 └──────────────────────────────────────────────┘
                     │
                     ▼ for each (id, vector):
                       sink.write(id, vector)  ──▶  s3://bucket/embeddings/{id}.npy
                       store.mark_processed(id, uri)
```

---

## State Machine

```
        claim()                   mark_processed()
(none) ──────────▶ PENDING ──────────────────────▶ PROCESSED
                    │  ▲
                    │  │ (DynamoDB only, automatic)
                    │  │ TTL expiry returns to (none)
                    │  │
                    └──┘ release()  ──────────────▶ (none)
```

| State | What it means | How it ends |
|---|---|---|
| (none) | The chunk has never been seen, or its PENDING claim was released | A successful `claim()` moves to PENDING |
| PENDING | A worker has claimed the chunk but not yet committed an embedding | `mark_processed` → PROCESSED, `release` → (none), or DynamoDB TTL expiry → (none) |
| PROCESSED | A successful embedding exists at `embedding_uri` | Terminal — never transitions back |

---

## Backend Trade-offs

| Backend | Atomic claims | Multi-worker | Storage cost (50 k chunks) | Operational complexity |
|---|---|---|---|---|
| `DynamoDBStore` | ✓ via `ConditionExpression` | ✓ | ~$0.0025/mo + ~$0.14 ops | DDB table with PK + TTL |
| `S3ManifestStore` | ✗ (last-write-wins) | ✗ | ~$0 (one JSON object) | None beyond an S3 bucket |
| `LocalManifestStore` | ✓ in-process only | ✗ | filesystem | None |

**When to use which:**

- **DynamoDB** — production, multi-worker (Lambda fan-out, Kubernetes Jobs).
  Atomic conditional writes mean two workers can race for the same chunk
  and exactly one wins. TTL on PENDING records auto-reclaims work that
  died mid-flight, with no cron job.
- **S3 manifest** — single-writer batch (nightly cron, one Lambda). No
  DynamoDB cost, but concurrent writers WILL clobber each other.
- **Local** — dev and tests. Atomic via `os.replace`; in-process lock
  serialises threads.

---

## Idempotency Guarantees

1. **`store.is_processed(id)` is true ⇔ a successful embedding exists at the
   recorded URI.** The orchestrator can short-circuit on `True` without
   reading the embedding.
2. **`store.claim(id)` is atomic** for DynamoDB and `LocalManifestStore`.
   Concurrent callers see exactly one `True` return. (S3 manifest is
   best-effort due to last-write-wins.)
3. **A crash between `claim` and `mark_processed`** leaves a PENDING
   record. The next run reclaims it via the staleness path:
       - DynamoDB: TTL field expires → conditional write succeeds again.
       - Local / S3: claimed_at older than `PENDING_TTL_SEC` → reclaimable.
4. **Sink writes are atomic** (LocalSink does tmp+rename, S3Sink uses a
   single PutObject). A failed write leaves no partial file.
5. **Content-addressed ids** mean even if the store gets corrupted, replay
   produces identical filenames — overwrites are no-ops, never duplicates.

---

## Trade-off Table

| Decision | Chosen | Alternative | Reasoning |
|---|---|---|---|
| Id strategy | `sha256(text + metadata)` | UUID at ingest | UUID would couple the id to ingest time; sha makes ingest replay-safe and dedupes identical chunks across documents |
| Hashed metadata fields | `source`, `section`, `chunk_index` | Hash text only | Two distinct paragraphs that happen to share text (templated boilerplate) should be distinct chunks |
| Hash algorithm | SHA-256 | SHA-1 / MD5 / xxh3 | SHA-256 has zero practical collision risk; speed (~500 MB/s) is never the bottleneck |
| Claim then embed | Yes | Embed first, then claim | Embedding is the expensive step; cheap-claim-first detects races before paying for the API call |
| Backend protocol | `IdempotencyStore` Protocol with 3 impls | One concrete class | Test with local, deploy with DDB, fall back to S3 — same orchestrator code |
| DynamoDB TTL | Auto-reclaim PENDING after `pending_ttl_sec` | Cron job to clear stale claims | TTL is a free DDB feature; no Lambda or cron to maintain |
| Batch size | 64 (env-tunable) | One per call (broken) or 1024 (max) | 64 fits OpenAI's 8192-token request limit at typical chunk sizes; 1024 risks one bad chunk taking down the whole batch |
| Failure handling | Release claim on any embed/sink failure | Mark as FAILED with retry counter | Simpler state machine; the next run *will* retry, and a permanently-bad chunk will keep failing with a loud error |
| Logging format | `event=key=value` | JSON | Same reasoning as Activity 7 — CloudWatch Insights and Datadog parse it for free |
| Sink atomicity | tmp + `os.replace` | Direct write | A crash mid-write would leave a partial `.npy` that crashes `np.load()`. Atomic rename is one extra syscall |

---

## Edge Cases Handled

| Case | Behaviour |
|---|---|
| Re-run with no changes | Every chunk hits `is_processed()` → skipped, zero API cost |
| New chunk added to corpus | Skipped chunks short-circuit; the new chunk claims, embeds, commits |
| Same paragraph in two documents (different `source`) | Distinct ids → embedded twice. Intentional: dedup is on content+metadata, not text alone |
| Same paragraph reposted with identical metadata | Identical id → second occurrence either reads the existing PROCESSED record or fails to claim → embedded once |
| Process killed mid-batch | PENDING records remain; `release()` is best-effort but TTL eventually clears them; next run re-claims |
| Concurrent workers race for the same id | DynamoDB conditional write makes exactly one win; the other increments `skipped_locked` |
| OpenAI API returns 429 | Exponential backoff (1, 2, 4, 8, 16 s); after `max_retries` the batch fails and all claims are released |
| Sink write fails (S3 transient error) | Claim is released; chunk re-tries on next run |
| Manifest file becomes corrupted (local backend) | Manual recovery: delete the manifest. Content-addressed sink files mean re-running rebuilds the manifest without producing duplicate vectors |
| Empty chunks | `compute_chunk_id` raises `ValueError` — caller decides how to handle (skip, fail, fix upstream) |
| Pipeline runs against an empty corpus | Returns `PipelineResult(total=0, embedded=0)` — no work, no errors |

---

## Cost Model

text-embedding-3-small @ $0.02/1M tokens, ~150 tok/chunk:

| Scenario | Broken | Fixed |
|---|---|---|
| First run, 50 k chunks | ~$0.15 | ~$0.15 |
| Re-run, 0 changes | ~$0.15 (every chunk re-embedded) | ~$0 (every chunk skipped) |
| Re-run, 100 new chunks added | ~$0.15 + 100 chunks (50,100 total) | ~$0.0003 (only the 100 new chunks) |
| Daily run × 365 days, 0 changes | ~$55/year | ~$0/year |
| DynamoDB ops (50 k chunks) | n/a | ~$0.13 ops + ~$0.0025/mo storage |

The real win isn't the dollars — it's that retrieval quality stays clean
because the FAISS index never accumulates duplicate vectors.

---

## Observability

```
event=pipeline_start chunks=50000 batch_size=64 model=text-embedding-3-small store=DynamoDBStore
event=chunk_claimed chunk_id=8b7f9c5e2a1b backend=dynamodb
event=chunk_processed chunk_id=8b7f9c5e2a1b uri=s3://fossilrag/embeddings/8b7f9c5e2a1b....npy backend=dynamodb
event=rate_limited attempt=1 backoff_sec=1.0
event=pipeline_complete total=50000 embedded=128 skipped_processed=49872 skipped_locked=0 failed=0
event=pipeline_summary total=50000 embedded=128 skipped_processed=49872 skipped_locked=0 failed=0
```

CloudWatch Insights — find the cost of each run:

```
fields @timestamp, @message
| filter @message like /event=pipeline_complete/
| parse @message "embedded=*" as embedded
| stats sum(embedded) by bin(1day)
```

Find chunks that keep failing (poison-pill detection):

```
fields @timestamp, @message
| filter @message like /event=embed_batch_failed/
| sort @timestamp desc
```

---

## Rollback Plan

The pipeline is read-only against the chunks file and additive against the
manifest + embeddings store. Three rollback paths:

1. **Bad embedding model swap** — change `EMBEDDING_MODEL` back. Re-runs
   skip already-processed chunks, so you'll have a mixed embedding-model
   index. To force a re-embed: drop the manifest and `OUTPUT_DIR`. Content-
   addressed filenames mean the new embeddings overwrite the old without
   duplicates.
2. **Bad chunk hashing change** — bumping `HASHED_METADATA_FIELDS` (adding
   or removing a field) is a hard break: every existing id changes. Roll
   forward by versioning the table name (`chunk-registry-v2`), not back.
3. **DynamoDB outage** — fall back to `S3ManifestStore` by changing one
   env var. Single-writer assumption applies; only safe if you can pause
   parallel workers until DDB recovers.
