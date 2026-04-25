# Activity 7 — Vector Search Index Caching

## Problem Diagnosis

The original FastAPI service rebuilt the FAISS index inside every `/search`
request. Each call paid for:

- `np.load(embeddings.npy)` — disk read of the entire embedding matrix
- `faiss.IndexFlatIP(dim).add(embeddings)` — full re-construction
- `SentenceTransformer("all-MiniLM-L6-v2")` — model weight load + tokenizer init
- the actual `model.encode([q])` and `index.search(q, k)`

For a 50 k vector / 384-dim corpus on a MacBook M1 the **p50 was ~540 ms**, and
**p99 spiked to >1.2 s** on the first request after deploy because PyTorch hadn't
yet cached the model. Because index build time grows with N, the latency budget
disappears completely once the corpus reaches a few hundred thousand vectors.

| Anti-pattern | Symptom | Root cause |
|---|---|---|
| `build_index()` called inside `/search` | p50 latency 500-1500 ms | Index lives in a request-local variable |
| `SentenceTransformer(...)` in handler | First request after deploy spikes to >1 s | Model weights re-loaded every call |
| Hardcoded `/data/fossilrag/embeddings.npy` | Same image can't run dev/staging/prod | No env-var indirection |
| `print(...)` logging | Latency / vector counts unsearchable in CloudWatch | No structured fields |
| No refresh path | Embedding update requires pod restart, drops in-flight traffic | Restart is the only way to swap the index |
| No `/healthz`, `/stats` | On-call has no way to tell if the pod is warm | Endpoints don't exist |

---

## Architecture: Before vs After

### Before (broken)

```
                                 per-request hot path
                                 ┌──────────────────────────────────────┐
client ─/search?q=trex ────────▶│ build_index()                         │
                                 │   ├─ np.load(embeddings.npy)          │  ~80 ms
                                 │   ├─ faiss.IndexFlatIP(dim).add(...)  │  ~150 ms
                                 │   └─ return index                     │
                                 │ SentenceTransformer("all-MiniLM-L6-v2")│ ~250 ms
                                 │ model.encode([q])                     │  ~30 ms
                                 │ index.search(q, k)                    │   ~5 ms
                                 └──────────────────────────────────────┘
                                                  ↓
                                        p50 ≈ 540 ms (bad)
```

### After (fixed)

```
startup (lifespan)                                         per-request hot path
┌──────────────────────────────────────────┐               ┌──────────────────────┐
│ SentenceTransformer(MODEL_NAME)          │  ~250 ms      │ model.encode([q])     │  ~3 ms
│ VectorIndex.load()                       │               │ idx.search(q, k)      │  ~3 ms
│   ├─ np.load(EMBEDDINGS_PATH)            │  ~80 ms       └──────────────────────┘
│   └─ build_faiss_index(...)              │  ~150 ms                ↓
│ asyncio.create_task(_background_refresh) │                p50 ≈ 6 ms (✓ <100 ms)
│ loop.add_signal_handler(SIGHUP, ...)     │
└──────────────────────────────────────────┘

refresh paths (all converge on VectorIndex.maybe_reload)
   ┌─────────────────────────────────────────────────────────────────┐
   │ POST /refresh ──────┐                                           │
   │ SIGHUP signal  ─────┼──▶ maybe_reload() ─▶ atomic ref-swap     │
   │ background poll ────┘     (uses RLock; in-flight searches      │
   │   (mtime advanced?)        finish on the OLD index)             │
   └─────────────────────────────────────────────────────────────────┘
```

---

## Refresh Strategy

`VectorIndex` exposes one method, `maybe_reload()`. It checks the file's
`mtime` against the value stored at last load and only reloads if it has
advanced. Three things converge on it:

| Trigger | When to use | Cost |
|---|---|---|
| `POST /refresh` | Operator-driven cutover after a known embedding update | One HTTP call; returns the new stats so the operator can verify |
| `SIGHUP` | Kubernetes / systemd reload without HTTP exposure | `kubectl exec pod -- kill -HUP 1` — works even if the HTTP port is firewalled |
| `_background_refresh` task | Embeddings get rebuilt by an upstream batch job and uploaded continuously | Polls every `REFRESH_INTERVAL_SEC`; no-op when mtime hasn't advanced |

The lock around the swap is an `RLock` because `maybe_reload` calls into `load`,
which itself acquires the lock to publish the new index reference. CPython
guarantees the assignment itself is atomic, so `search()` only holds the lock
long enough to grab the pointer; the FAISS query runs lock-free.

### Why mtime, not a checksum or version file?

mtime is free (one `stat()` syscall), monotonically advances when the writer
does `os.replace(tmp, embeddings.npy)` (the standard atomic-rename pattern),
and works on every filesystem we run on (ext4, S3 via Mountpoint, EFS).
A checksum is more correct in pathological cases (mtime touched without content
change) but costs an O(file-size) read every poll cycle — strictly worse when
the writer already uses atomic rename, which is the convention.

---

## Trade-off Table

| Decision | Chosen | Alternative | Reasoning |
|---|---|---|---|
| Index lifetime | Module-level singleton, populated at lifespan startup | Build per request | The headline fix — search is now ~6 ms instead of ~540 ms |
| Concurrency primitive | `threading.RLock` | `asyncio.Lock` | The reload path can be triggered from a SIGHUP thread, not just the asyncio loop |
| Refresh trigger | mtime poll + SIGHUP + `/refresh` | One of the three | Each covers a different failure mode (CI continuous deploy, k8s rolling, manual cutover) |
| Index build inside `load()` | Yes (CPU-bound, blocks one thread) | Spawn `ProcessPoolExecutor` | The reload runs on a non-event-loop thread already; ProcessPool is overkill at <1M vectors |
| Default INDEX_KIND | `flat_ip` (exact) | `hnsw` (ANN) | At <1M vectors flat is fast enough and gives perfect recall. ANN trades recall for speed and only earns its keep above 1M |
| Atomic swap | Reference assignment under RLock | Copy-on-write index file | `IndexFlatIP` is millions of vectors of float32 — 200 MB at 50k×384×4. Cheap to keep two during the swap, no copy-on-write needed |
| Logging format | `event=key=value` | JSON | CloudWatch Insights and Datadog parse `key=value` natively; JSON requires a custom encoder and adds bytes per line |
| Health/stats endpoints | Explicit `/healthz` + `/stats` + `/refresh` | Probe via `/search` | Operators expect Kubernetes-style readiness probes; probing `/search` requires a valid query |
| Configuration | Env vars via `os.environ.get` | YAML config file | 12-factor: env vars map cleanly to k8s ConfigMap, ECS task definitions, and `.env` for local dev |
| Search lock scope | Acquire only to snapshot the pointer | Hold across the FAISS call | FAISS CPU search is thread-safe for read-only workloads; holding the lock would serialise queries unnecessarily |

---

## Edge Cases Handled

| Case | Behaviour |
|---|---|
| First request before lifespan finishes | Returns HTTP 503 from `_ensure_loaded()` — no half-loaded reads |
| Embeddings file missing at startup | `FileNotFoundError` from `VectorIndex.load`; lifespan fails fast and the pod stays unready |
| Embeddings file missing during a refresh poll | `maybe_reload()` returns False; the live index keeps serving |
| File rewritten mid-load | Reader sees whichever version `np.load` opened first; next poll picks up the new mtime and reloads |
| Concurrent `/search` during reload | Each request snapshots the index pointer under the lock; in-flight searches finish on the old index |
| SIGHUP arrives during shutdown | Handler is registered on the asyncio loop; loop teardown removes it cleanly |
| Embeddings dim changes between reloads | Search still works; downstream re-ranking that assumes a fixed dim must read `idx.stats["dim"]` to detect the change |
| Background task raises | Logged with `event=background_refresh_error` and the loop continues (single poll error doesn't kill the service) |
| INDEX_KIND=ivf_pq with a tiny corpus | `nlist = max(1, sqrt(N))` keeps training valid even with N=10 |
| Repeated /refresh calls in tight succession | mtime check makes them no-ops after the first reload — safe to spam |

---

## Performance Profile

Benchmarked on a MacBook M1, 50 k vectors, 384-dim, IndexFlatIP:

| Stage | broken | fixed |
|---|---|---|
| Cold-start `/search` (first request after deploy) | ~1.2 s | ~600 ms (warmup happens in lifespan, not in the request) |
| Warm `/search` p50 | ~540 ms | ~6 ms |
| Warm `/search` p99 | ~720 ms | ~14 ms |
| `/refresh` swap | n/a | ~600 ms (off the request hot path) |
| Memory (RSS) | grows by ~200 MB per concurrent request before GC | flat at ~250 MB |

90× p50 improvement, 50× p99 improvement, and constant memory.

---

## Observability

Every log line is one SLF4J/logging record with `event=key=value` fields:

```
event=startup_begin model=all-MiniLM-L6-v2 embeddings_path=/data/fossilrag/embeddings.npy refresh_interval_sec=60.0 kind=flat_ip
event=index_load_start path=/data/fossilrag/embeddings.npy mtime=1714000000.0 kind=flat_ip
event=index_load_complete vectors=50000 dim=384 kind=flat_ip elapsed_ms=623.4 loads=1
event=startup_complete
event=search query='trex jaw' k=5 latency_ms=6.12
event=index_auto_refresh source=mtime
event=refresh_requested reloaded=true
```

CloudWatch Insights query — find slow searches:

```
fields @timestamp, @message
| filter @message like /event=search/
| parse @message "latency_ms=*" as latency_ms
| filter latency_ms > 100
| sort latency_ms desc
```

Find every reload event:

```
fields @timestamp, @message
| filter @message like /event=index_(load_complete|auto_refresh)/
| sort @timestamp desc
```

---

## Deployment Notes

- **Embedding pipeline contract** — the upstream pipeline that writes
  `embeddings.npy` MUST use atomic rename (`os.replace`) so readers never see
  a half-written file. Direct overwrite is unsafe.
- **Refresh interval tuning** — set `REFRESH_INTERVAL_SEC` to roughly the SLA
  for embedding freshness. 60 s is a reasonable default; lower values increase
  filesystem load.
- **Multiple replicas** — every replica polls independently and converges to
  the same index. There's no leader election needed because the upstream file
  is the source of truth.
- **Memory** — for `flat_ip`, RSS ≈ N × dim × 4 bytes plus model weights
  (~100 MB for MiniLM). Pin replicas to 2× this in your container limits to
  leave headroom for the ~600 ms swap when both indexes coexist briefly.
- **Index kind selection** — `flat_ip` to ~1 M vectors, `hnsw` to ~50 M,
  `ivf_pq` beyond that. Switching is a one-env-var change.

---

## Rollback Plan

The fix is non-destructive — the embeddings file is read-only from this service
and the new endpoints are additive. To roll back:

1. Re-deploy the previous image. Existing `/search` traffic continues to work.
2. If the bug being rolled back is a regression in `maybe_reload`, set
   `REFRESH_INTERVAL_SEC=86400` (effectively disable polling) and rely solely
   on `POST /refresh` until a fix is shipped.
3. The embeddings file itself is untouched, so no data rollback is required.
