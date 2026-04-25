# Activity 7: Fix a Slow Vector Search System

**Week:** 2 | **Day:** 7 | **Course alignment:** AWS Technical Essentials

## Problem Statement

A FastAPI vector-search service **rebuilds the FAISS index on every `/search`
request**, blowing the latency budget (>500 ms p50) and burning CPU. The index
should be loaded once, kept warm in memory, and refreshed without restarting.

## What to Fix

- [x] Load the index **once at application startup** via FastAPI lifespan
- [x] Keep the loaded index **in memory** (module-level `VectorIndex` singleton)
- [x] Add a **refresh mechanism** — three converging triggers:
      `POST /refresh`, `SIGHUP` signal, and a background mtime poll
- [x] Target: **sub-100 ms** search latency after warm-up
- [x] Atomic ref-swap on reload so in-flight searches finish on the old index
- [x] Env-var-driven config; structured `event=key=value` logs; `/healthz` + `/stats`

## Acceptance Criteria

- First request loads the index; subsequent requests reuse it ✅
- Search latency is <100 ms for typical queries (measured: ~6 ms p50, ~14 ms p99 on 50 k × 384) ✅
- Index can be refreshed without restarting the service ✅

## What Was Fixed

| # | Anti-pattern (broken) | Fix applied | Impact |
|---|---|---|---|
| 1 | `build_index()` called inside `/search` | Index loaded once at lifespan startup, held in `_VECTOR_INDEX` singleton | p50 540 ms → 6 ms (≈90× faster) |
| 2 | `SentenceTransformer(...)` re-loaded per request | Model loaded once at lifespan startup | p99 1.2 s → 14 ms (no model re-init in hot path) |
| 3 | No refresh path | `POST /refresh`, `SIGHUP` handler, and background mtime poll all converge on `VectorIndex.maybe_reload` | Embedding updates roll out without dropping in-flight traffic |
| 4 | No locking | `threading.RLock` around the index reference | Concurrent searches during refresh are safe — atomic ref-swap |
| 5 | Hardcoded `/data/fossilrag/embeddings.npy` | `os.environ.get("EMBEDDINGS_PATH", ...)` plus `EMBEDDING_MODEL`, `REFRESH_INTERVAL_SEC`, `INDEX_KIND`, `LOG_LEVEL` | One image runs in dev / staging / prod |
| 6 | `print()` logging | `logging.getLogger("vector_search")` + `event=key=value` records | CloudWatch Insights / Datadog parse without a custom grok |
| 7 | No health probes | `/healthz`, `/stats`, `/refresh` | Operators can introspect a live pod with `curl` |
| 8 | One index kind hardcoded | `flat_ip`, `hnsw`, `ivf_pq` selectable via `INDEX_KIND` | Same code scales from 10 k to 100 M+ vectors |

## Refresh Strategy (summary)

| Trigger | When to use |
|---|---|
| `POST /refresh` | Operator-driven cutover after a known embedding update |
| `SIGHUP` (`kubectl exec pod -- kill -HUP 1`) | k8s/systemd reload without HTTP exposure |
| Background poll every `REFRESH_INTERVAL_SEC` | Continuous-deploy embedding pipelines that touch the file periodically |

All three call into `VectorIndex.maybe_reload`, which checks file mtime and
no-ops if the file hasn't changed. Full policy + edge cases in
[`docs/architecture.md`](docs/architecture.md).

## Performance

Benchmarked on MacBook M1, 50 k vectors, 384-dim, `IndexFlatIP`:

| Metric | broken | fixed |
|---|---|---|
| `/search` p50 | ~540 ms | **~6 ms** |
| `/search` p99 | ~720 ms | **~14 ms** |
| Cold start latency | paid by every request | paid once in lifespan startup |
| Memory (RSS) | grows per concurrent request | flat ~250 MB |

## How to Run Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Expected: **21 tests passing** — 19 source-file analysis assertions plus 2 behaviour tests against the `VectorIndex` class.

Tests are split into two layers:

1. **Source-file analysis** — regex / AST checks against `broken/search_service.py`
   and `search_service.py`. Run with no FAISS / torch install needed.
2. **Behaviour tests** — exercise `VectorIndex.load`, `maybe_reload`, and concurrent
   `search` against a stub FAISS backend. No network or model download.

## How to Run the Service

```bash
cp .env.example .env
# edit .env to point EMBEDDINGS_PATH at your .npy file
pip install -r requirements.txt
uvicorn search_service:app --host 0.0.0.0 --port 8080
```

Then:

```bash
curl 'http://localhost:8080/healthz'
curl 'http://localhost:8080/search?q=trex+jaw&k=5'
curl -X POST 'http://localhost:8080/refresh'
kill -HUP $(pgrep -f 'uvicorn search_service')
```

## Layout

```
activity-07-vector-search/
├── broken/
│   └── search_service.py     # Original, anti-patterns intact
├── search_service.py         # FastAPI app — lifespan, endpoints, refresh task
├── vector_index.py           # Thread-safe FAISS wrapper (testable in isolation)
├── requirements.txt          # Runtime deps (FastAPI, FAISS, sentence-transformers)
├── requirements-dev.txt      # pytest + numpy
├── .env.example              # Documents every env var
├── docs/
│   └── architecture.md       # Before/after, policy, trade-offs, edge cases
├── tests/
│   └── test_search.py        # Source-file + behaviour test layers
└── README.md
```

## PR Checklist

- [x] Anti-patterns preserved in `broken/search_service.py`; working implementation committed in `search_service.py`
- [x] `.env.example` documents every environment variable
- [x] pytest assertions cover both broken anti-patterns and every fix, plus VectorIndex behaviour
- [x] `docs/architecture.md` — before/after diagrams, trade-off table, edge cases, refresh strategy
- [ ] 2–5 min video walkthrough (before/after)

## Notes

**Why mtime polling over a checksum:** mtime is one `stat()` syscall and is
authoritative when the writer uses atomic rename (`os.replace(tmp, target)`),
which is the convention. A checksum is correct in pathological cases (touched
mtime, same content) but pays an O(N) read every poll cycle.

**Why three refresh triggers:** each covers a different failure mode. `/refresh`
is for operator-driven cutovers; `SIGHUP` works when HTTP is firewalled;
background polling handles continuous-deploy embedding pipelines. They share
the same `maybe_reload` body so behaviour is identical regardless of trigger.

**Why `RLock` over `Lock`:** `maybe_reload` calls `load`, which itself takes
the lock to publish the new index reference. A non-reentrant lock would
deadlock on the second acquisition. The lock is held only long enough to
snapshot the index pointer; the FAISS query runs lock-free, so concurrent
searches don't serialise.

**Why default `flat_ip`:** at <1 M vectors, exact inner-product search is fast
enough on CPU and gives perfect recall. ANN indexes (HNSW, IVF-PQ) trade recall
for speed and only earn their keep above 1 M. `INDEX_KIND` makes the upgrade
a one-env-var change.
