"""
Test suite for Activity 7 — vector search index caching.

The tests are deliberately written in two layers:

1. Source-file analysis (regex / AST). Verifies that the broken file still
   contains every anti-pattern we want to demonstrate, and that the fixed
   file uses every required pattern (lifespan, lock, env vars, etc.). This
   layer requires only Python — no FAISS, no torch, no model download —
   so it runs in any CI environment.

2. Behaviour tests against an in-process VectorIndex stand-in. These exercise
   the load-once / atomic-swap / mtime-based-reload contract using a fake
   FAISS-like backend to keep the suite import-cheap.

Run:
    pip install -r requirements-dev.txt
    pytest tests/ -v
"""

from __future__ import annotations

import ast
import re
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BROKEN = (ROOT / "broken" / "search_service.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "search_service.py").read_text(encoding="utf-8")
INDEX = (ROOT / "vector_index.py").read_text(encoding="utf-8")
# Concatenated view used by pattern-presence tests — they don't care which file
# a given pattern lives in, only that the production code uses it.
FIXED = SERVICE + "\n" + INDEX


# ===========================================================================
# Layer 1 — anti-patterns are preserved in broken/
# ===========================================================================


class TestBrokenAntiPatterns:
    def test_broken_calls_build_index_inside_search(self):
        # The headline bug: rebuild on every request.
        match = re.search(
            r"def\s+search\s*\([^)]*\)\s*:.*?(?=\n@|\Z)",
            BROKEN,
            flags=re.DOTALL,
        )
        assert match, "search() not found in broken file"
        body = match.group(0)
        assert "build_index()" in body, (
            "broken/search_service.py must call build_index() inside the request handler"
        )

    def test_broken_loads_model_per_request(self):
        match = re.search(
            r"def\s+search\s*\([^)]*\)\s*:.*?(?=\n@|\Z)",
            BROKEN,
            flags=re.DOTALL,
        )
        body = match.group(0)
        assert "SentenceTransformer(" in body, (
            "broken file should load the embedding model inside the handler"
        )

    def test_broken_uses_print_logging(self):
        assert "print(" in BROKEN, "broken file should use print() for logging"

    def test_broken_has_no_lifespan(self):
        assert "lifespan" not in BROKEN
        assert "@app.on_event" not in BROKEN

    def test_broken_has_no_refresh_endpoint(self):
        # Strip the docstring (which mentions missing endpoints by name) before
        # checking for actual decorators / handler defs.
        code = re.sub(r'^"""[\s\S]*?"""', "", BROKEN, count=1).strip()
        assert "@app.post" not in code, "broken file should have no POST routes"
        assert re.search(r'@app\.\w+\(\s*["\']/refresh', code) is None
        assert re.search(r"\bdef\s+refresh\s*\(", code) is None

    def test_broken_has_hardcoded_paths(self):
        assert "/data/fossilrag/embeddings.npy" in BROKEN
        # Must not source the path from the environment.
        assert "os.environ" not in BROKEN
        assert "getenv" not in BROKEN


# ===========================================================================
# Layer 1 — fixed file uses every required pattern
# ===========================================================================


class TestFixedPatterns:
    def test_uses_fastapi_lifespan(self):
        assert "lifespan" in FIXED
        assert "asynccontextmanager" in FIXED
        assert "FastAPI(lifespan=lifespan" in FIXED

    def test_has_module_level_singletons(self):
        assert re.search(r"^_VECTOR_INDEX\s*[:=]", FIXED, flags=re.MULTILINE)
        assert re.search(r"^_MODEL\s*[:=]", FIXED, flags=re.MULTILINE)

    def test_uses_threading_lock(self):
        assert "threading.RLock()" in FIXED or "threading.Lock()" in FIXED

    def test_search_does_not_call_build_index(self):
        # The search handler should look up the singleton, not rebuild.
        # Walk the AST so we get *only* the body of the search() function.
        tree = ast.parse(SERVICE)
        search_fn = next(
            (node for node in ast.walk(tree)
             if isinstance(node, ast.FunctionDef) and node.name == "search"),
            None,
        )
        assert search_fn is not None, "search() function not found in fixed file"
        body_src = ast.unparse(search_fn)
        assert "build_faiss_index" not in body_src, (
            "search handler must not invoke build_faiss_index per request"
        )
        assert "np.load" not in body_src, (
            "search handler must not load embeddings from disk per request"
        )
        assert "SentenceTransformer(" not in body_src, (
            "search handler must not instantiate the model per request"
        )

    def test_has_refresh_endpoint(self):
        assert re.search(r'@app\.post\(\s*["\']/refresh["\']\s*\)', FIXED)
        assert "def refresh" in FIXED

    def test_has_healthz_endpoint(self):
        assert re.search(r'@app\.get\(\s*["\']/healthz["\']\s*\)', FIXED)

    def test_has_stats_endpoint(self):
        assert re.search(r'@app\.get\(\s*["\']/stats["\']\s*\)', FIXED)

    def test_uses_env_vars_for_config(self):
        assert "os.environ" in FIXED or "os.getenv" in FIXED
        assert "EMBEDDINGS_PATH" in FIXED
        assert "EMBEDDING_MODEL" in FIXED
        assert "REFRESH_INTERVAL_SEC" in FIXED
        assert "INDEX_KIND" in FIXED

    def test_uses_structured_logging(self):
        assert "logging.getLogger" in FIXED
        assert "event=" in FIXED, "logs should carry event=key=value fields"
        # And no print().
        assert "print(" not in FIXED

    def test_has_background_refresh_task(self):
        assert "asyncio.create_task" in FIXED
        assert "_background_refresh" in FIXED
        assert "REFRESH_INTERVAL_SEC" in FIXED

    def test_has_sighup_handler(self):
        assert "SIGHUP" in FIXED
        assert "add_signal_handler" in FIXED

    def test_supports_multiple_index_kinds(self):
        assert "flat_ip" in FIXED
        assert "hnsw" in FIXED
        assert "ivf_pq" in FIXED

    def test_module_parses_cleanly(self):
        # Catch syntax errors without importing FAISS.
        ast.parse(SERVICE)
        ast.parse(INDEX)
        ast.parse(BROKEN)


# ===========================================================================
# Layer 2 — behaviour of VectorIndex against a fake FAISS backend
# ===========================================================================


@pytest.fixture
def fake_faiss(monkeypatch):
    """Stub `faiss` and `numpy` so VectorIndex can be exercised without FAISS."""

    class FakeIndex:
        def __init__(self, dim):
            self.dim = dim
            self.vectors = []

        def add(self, arr):
            for v in arr:
                self.vectors.append(v)

        def search(self, query, k):
            import numpy as np
            scores = np.array([[float(i) for i in range(min(k, len(self.vectors)))]])
            ids = np.array([[i for i in range(min(k, len(self.vectors)))]])
            return scores, ids

    class FakeFaiss:
        def IndexFlatIP(self, dim): return FakeIndex(dim)
        def IndexHNSWFlat(self, dim, M): return FakeIndex(dim)
        def IndexIVFPQ(self, *args, **kwargs): return FakeIndex(args[1])
        Index = FakeIndex

    return FakeFaiss()


def test_vector_index_load_and_swap(tmp_path, fake_faiss, monkeypatch):
    import sys
    import numpy as np

    monkeypatch.setitem(sys.modules, "faiss", fake_faiss)
    monkeypatch.syspath_prepend(str(ROOT))

    if "vector_index" in sys.modules:
        del sys.modules["vector_index"]

    embeddings_path = tmp_path / "embeddings.npy"
    np.save(embeddings_path, np.random.rand(10, 4).astype("float32"))

    import importlib
    mod = importlib.import_module("vector_index")
    importlib.reload(mod)

    idx = mod.VectorIndex(embeddings_path, kind="flat_ip")
    idx.load()
    assert idx.stats["vectors"] == 10
    assert idx.stats["loads"] == 1
    first_mtime = idx.stats["loaded_mtime"]

    # No-op reload when mtime hasn't advanced.
    assert idx.maybe_reload() is False
    assert idx.stats["loads"] == 1

    # Bump mtime + grow embeddings -> reload should fire.
    time.sleep(0.05)  # ensure the new mtime is observably different
    np.save(embeddings_path, np.random.rand(20, 4).astype("float32"))
    new_mtime = embeddings_path.stat().st_mtime
    assert new_mtime > first_mtime
    assert idx.maybe_reload() is True
    assert idx.stats["vectors"] == 20
    assert idx.stats["loads"] == 2


def test_vector_index_search_thread_safety(tmp_path, fake_faiss, monkeypatch):
    """Concurrent searches against an index that's also being reloaded
    must not raise — the lock guarantees the swap is atomic."""
    import sys
    import numpy as np

    monkeypatch.setitem(sys.modules, "faiss", fake_faiss)
    monkeypatch.syspath_prepend(str(ROOT))

    if "vector_index" in sys.modules:
        del sys.modules["vector_index"]
    import importlib
    mod = importlib.import_module("vector_index")
    importlib.reload(mod)

    embeddings_path = tmp_path / "embeddings.npy"
    np.save(embeddings_path, np.random.rand(10, 4).astype("float32"))

    idx = mod.VectorIndex(embeddings_path, kind="flat_ip")
    idx.load()

    errors: list[Exception] = []

    def hammer_search():
        try:
            for _ in range(50):
                q = np.zeros((1, 4), dtype="float32")
                idx.search(q, 3)
        except Exception as e:
            errors.append(e)

    def hammer_reload():
        try:
            for _ in range(10):
                np.save(embeddings_path, np.random.rand(15, 4).astype("float32"))
                time.sleep(0.005)
                idx.maybe_reload()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=hammer_search) for _ in range(4)]
    threads.append(threading.Thread(target=hammer_reload))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"thread safety violations: {errors}"
    assert idx.stats["loads"] >= 2
