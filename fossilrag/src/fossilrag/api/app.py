"""FastAPI app: ``/excavate`` (top-k retrieval), ``/ingest`` (spine), ``/mutate``.

Lifespan wiring follows the FastAPI 0.136 + house pattern: the embedder and
the connected/bootstrapped vector store are built once at startup, stored on
``app.state``, and reached in handlers via ``Depends`` providers — never a
per-request handshake. ``/mutate`` retrieves relevant fossils and returns an
LLM summary/edit (pluggable mock/Bedrock/Anthropic) with Prompt Fossilization
(output caching); the further mutation endpoints (time-travel, diff, dataset)
are deepened in later PRs without disturbing this surface.
"""

from __future__ import annotations

import contextlib
import time

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from fossilrag import __version__
from fossilrag.config import Settings, get_settings
from fossilrag.embedding import make_embedder
from fossilrag.embedding.base import Embedder
from fossilrag.llm import fossil_key, make_llm, make_prompt_cache
from fossilrag.llm.base import LLMProvider
from fossilrag.llm.cache import PromptCache
from fossilrag.logging import configure_logging, get_logger
from fossilrag.models import ExcavateResponse, IngestResult, MutateResponse
from fossilrag.mutate import MOCK_NOTE
from fossilrag.pipeline import ingest_document
from fossilrag.vectorstore import make_vector_store
from fossilrag.vectorstore.base import VectorStore

log = get_logger("api")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info("event=startup_begin service=%s version=%s", settings.service_name, __version__)
    app.state.settings = settings
    app.state.embedder = make_embedder(settings)
    app.state.llm = make_llm(settings)
    app.state.prompt_cache = make_prompt_cache(settings)
    # Connect + bootstrap the store. Failing here means the app fails to start
    # (rather than serving 503s forever), which is the behaviour we want.
    app.state.store = await make_vector_store(settings)
    log.info(
        "event=startup_complete embed_model=%s embed_dim=%d",
        app.state.embedder.model_id,
        app.state.embedder.dimensions,
    )
    try:
        yield
    finally:
        store = getattr(app.state, "store", None)
        if store is not None:
            with contextlib.suppress(Exception):
                await store.close()
        app.state.store = None
        log.info("event=shutdown_complete")


app = FastAPI(
    title="FossilRAG",
    version=__version__,
    summary="Serverless document enrichment & retrieval — the Dinosaur Whisperer's fossil excavator.",
    lifespan=lifespan,
)


# -- dependency providers -------------------------------------------------


def get_store(request: Request) -> VectorStore:
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="vector store not ready")
    return store


def get_embedder(request: Request) -> Embedder:
    embedder = getattr(request.app.state, "embedder", None)
    if embedder is None:
        raise HTTPException(status_code=503, detail="embedder not ready")
    return embedder


def settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_llm(request: Request) -> LLMProvider:
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        raise HTTPException(status_code=503, detail="llm not ready")
    return llm


def get_prompt_cache(request: Request) -> PromptCache:
    cache = getattr(request.app.state, "prompt_cache", None)
    if cache is None:
        raise HTTPException(status_code=503, detail="prompt cache not ready")
    return cache


# -- request models -------------------------------------------------------


class IngestRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=512)
    text: str = Field(min_length=1)
    content_type: str = "text/plain"
    user_id: str | None = None
    layer_version: int = Field(default=1, ge=1)


class MutateRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1024)
    k: int = Field(default=5, ge=1, le=50)
    instruction: str | None = Field(default=None, max_length=512)


# -- endpoints ------------------------------------------------------------


@app.get("/")
async def root(settings: Settings = Depends(settings_dep)) -> dict:
    return {
        "service": settings.service_name,
        "version": __version__,
        "embed_model": settings.embed_model,
        "embed_dim": settings.embed_dim,
        "vector_backend": "pgvector",
        "endpoints": ["/excavate", "/ingest", "/mutate", "/healthz"],
    }


@app.get("/healthz")
async def healthz(request: Request) -> dict:
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="store not ready")
    try:
        ok = await store.healthcheck()
    except Exception:
        log.exception("event=healthz_failed")
        raise HTTPException(status_code=500, detail="store unhealthy") from None
    return {"status": "ok" if ok else "degraded", **store.stats()}


@app.post("/ingest", response_model=IngestResult)
async def ingest(
    req: IngestRequest,
    store: VectorStore = Depends(get_store),
    embedder: Embedder = Depends(get_embedder),
) -> IngestResult:
    """Ingest a document through the full spine and index its fossils."""
    try:
        return await ingest_document(
            store=store,
            embedder=embedder,
            filename=req.filename,
            data=req.text.encode("utf-8"),
            content_type=req.content_type,
            user_id=req.user_id,
            layer_version=req.layer_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@app.get("/excavate", response_model=ExcavateResponse)
async def excavate(
    q: str = Query(..., min_length=1, max_length=1024, description="Natural-language query."),
    k: int = Query(5, ge=1, le=50, description="Number of fossils to return."),
    store: VectorStore = Depends(get_store),
    embedder: Embedder = Depends(get_embedder),
) -> ExcavateResponse:
    """Embed the query and return the top-k nearest fossil chunks + metadata."""
    t0 = time.perf_counter()
    query_vec = embedder.encode_one(q)
    try:
        hits = await store.search(query_vec, k)
    except Exception:
        log.exception("event=excavate_failed q=%r", q)
        raise HTTPException(status_code=500, detail="excavation failed") from None
    latency_ms = (time.perf_counter() - t0) * 1000
    log.info("event=excavate q=%r k=%d hits=%d latency_ms=%.2f", q, k, len(hits), latency_ms)
    return ExcavateResponse(query=q, k=k, hits=hits, latency_ms=latency_ms)


@app.post("/mutate", response_model=MutateResponse)
async def mutate(
    req: MutateRequest,
    store: VectorStore = Depends(get_store),
    embedder: Embedder = Depends(get_embedder),
    llm: LLMProvider = Depends(get_llm),
    cache: PromptCache = Depends(get_prompt_cache),
) -> MutateResponse:
    """Retrieve relevant fossils and return an LLM summary/edit grounded in them.

    Prompt Fossilization: the (model, query, instruction, retrieved-fossils)
    tuple keys a cache; a repeat request is served from the cache instantly
    (``cached=True``) with no LLM call. The default ``mock`` provider keeps this
    callable at $0; set ``llm_provider=bedrock`` for a real Claude summary.
    """
    t0 = time.perf_counter()
    query_vec = embedder.encode_one(req.query)
    try:
        hits = await store.search(query_vec, req.k)
    except Exception:
        log.exception("event=mutate_failed q=%r", req.query)
        raise HTTPException(status_code=500, detail="mutation failed") from None

    is_mock = llm.model_id.startswith("mock")
    key = fossil_key(llm.model_id, req.query, req.instruction, hits)
    # Fail open on the cache: a backend hiccup must not break /mutate.
    try:
        cached_summary = cache.get(key)
    except Exception:
        log.warning("event=prompt_cache_get_failed key=%s", key[:12])
        cached_summary = None
    if cached_summary is not None:
        summary, cached = cached_summary, True
    else:
        try:
            result = llm.summarise(query=req.query, instruction=req.instruction, hits=hits)
        except Exception:
            log.exception("event=mutate_llm_failed q=%r model=%s", req.query, llm.model_id)
            raise HTTPException(status_code=502, detail="LLM provider error") from None
        summary, cached = result.text, False
        # A cache-write failure must NOT discard the (paid) summary we just got.
        with contextlib.suppress(Exception):
            cache.put(key, summary)

    latency_ms = (time.perf_counter() - t0) * 1000
    log.info(
        "event=mutate q=%r k=%d hits=%d model=%s cached=%s mock=%s latency_ms=%.2f",
        req.query,
        req.k,
        len(hits),
        llm.model_id,
        cached,
        is_mock,
        latency_ms,
    )
    return MutateResponse(
        query=req.query,
        instruction=req.instruction,
        summary=summary,
        model_id=llm.model_id,
        mock=is_mock,
        cached=cached,
        used_chunks=hits,
        note=MOCK_NOTE if is_mock else "",
        latency_ms=latency_ms,
    )
