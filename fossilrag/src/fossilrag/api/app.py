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
import json
import time

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from fossilrag import __version__
from fossilrag.config import Settings, get_settings
from fossilrag.dataset import build_pairs, to_jsonl
from fossilrag.embedding import make_embedder
from fossilrag.embedding.base import Embedder
from fossilrag.enrichment import extract_markers
from fossilrag.ingest import extract_document
from fossilrag.llm import fossil_key, make_llm, make_prompt_cache
from fossilrag.llm.base import LLMProvider
from fossilrag.llm.cache import PromptCache
from fossilrag.logging import configure_logging, get_logger
from fossilrag.models import (
    ChatMessage,
    ChatResponse,
    DatasetResponse,
    EnrichmentRecord,
    ExcavateResponse,
    FossilDiffResponse,
    IngestResult,
    MutateResponse,
    SlideMutateResponse,
    TimeTravelResponse,
)
from fossilrag.mutate import MOCK_NOTE
from fossilrag.pipeline import ingest_document
from fossilrag.timetravel import unified_fossil_diff, unified_text_diff
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
    # Stable logical-doc id tying versions together (defaults to filename).
    source_id: str | None = Field(default=None, max_length=512)
    layer_version: int = Field(default=1, ge=1)


class MutateRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1024)
    k: int = Field(default=5, ge=1, le=50)
    instruction: str | None = Field(default=None, max_length=512)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    k: int = Field(default=5, ge=1, le=50)
    # Optional: scope retrieval to one document ("select an existing fossil").
    source_id: str | None = Field(default=None, max_length=512)


class SlideMutateRequest(BaseModel):
    text: str = Field(min_length=1, description="Slide text to revise.")
    instruction: str = Field(min_length=1, max_length=512)
    # When source_id + persist, the suggestion is saved as a new fossil layer.
    source_id: str | None = Field(default=None, max_length=512)
    persist: bool = False


class DatasetRequest(BaseModel):
    source_id: str = Field(min_length=1)
    version: int | None = Field(default=None, ge=1)  # default: latest layer
    fmt: str = "chat"  # chat | alpaca
    templates: list[str] | None = None  # subset of dataset.TEMPLATES keys


# -- endpoints ------------------------------------------------------------


@app.get("/")
async def root(settings: Settings = Depends(settings_dep)) -> dict:
    return {
        "service": settings.service_name,
        "version": __version__,
        "embed_model": settings.embed_model,
        "embed_dim": settings.embed_dim,
        "vector_backend": "pgvector",
        "endpoints": [
            "/excavate",
            "/ingest",
            "/mutate",
            "/timetravel",
            "/diff",
            "/enrich",
            "/markers",
            "/chat",
            "/slide/mutate",
            "/dataset",
            "/healthz",
        ],
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
            source_id=req.source_id,
            layer_version=req.layer_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@app.get("/excavate", response_model=ExcavateResponse)
async def excavate(
    q: str = Query(..., min_length=1, max_length=1024, description="Natural-language query."),
    k: int = Query(5, ge=1, le=50, description="Number of fossils to return."),
    source_id: str | None = Query(None, description="Scope to one document (optional)."),
    store: VectorStore = Depends(get_store),
    embedder: Embedder = Depends(get_embedder),
) -> ExcavateResponse:
    """Embed the query and return the top-k nearest fossil chunks + metadata."""
    t0 = time.perf_counter()
    query_vec = embedder.encode_one(q)
    try:
        hits = await store.search(query_vec, k, source_id)
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


@app.get("/timetravel", response_model=TimeTravelResponse)
async def timetravel(
    source_id: str = Query(..., min_length=1, description="Stable logical-document id."),
    version: int | None = Query(None, ge=1, description="Fossil layer; default = latest."),
    store: VectorStore = Depends(get_store),
) -> TimeTravelResponse:
    """Return a document's fossils at a chosen layer (Time-Travel Query)."""
    versions = await store.list_layers(source_id)
    if not versions:
        raise HTTPException(status_code=404, detail=f"no fossil layers for source_id {source_id!r}")
    latest = versions[-1]
    v = version if version is not None else latest
    if v not in versions:
        raise HTTPException(status_code=404, detail=f"layer v{v} not found; available: {versions}")
    chunks = await store.get_layer(source_id, v)
    log.info("event=timetravel source_id=%s version=%d latest=%d", source_id, v, latest)
    return TimeTravelResponse(
        source_id=source_id,
        version=v,
        latest_version=latest,
        available_versions=versions,
        is_latest=(v == latest),
        chunks=chunks,
    )


@app.get("/diff", response_model=FossilDiffResponse)
async def diff(
    source_id: str = Query(..., min_length=1),
    from_version: int = Query(..., ge=1),
    to_version: int = Query(..., ge=1),
    store: VectorStore = Depends(get_store),
) -> FossilDiffResponse:
    """Show changes between two fossil layers of a document (Fossil Diff)."""
    from_chunks = await store.get_layer(source_id, from_version)
    to_chunks = await store.get_layer(source_id, to_version)
    if not from_chunks:
        raise HTTPException(status_code=404, detail=f"layer v{from_version} not found")
    if not to_chunks:
        raise HTTPException(status_code=404, detail=f"layer v{to_version} not found")
    result = unified_fossil_diff(from_version, to_version, from_chunks, to_chunks)
    log.info(
        "event=fossil_diff source_id=%s from=%d to=%d changed=%s",
        source_id,
        from_version,
        to_version,
        result.changed,
    )
    return FossilDiffResponse(
        source_id=source_id,
        from_version=from_version,
        to_version=to_version,
        changed=result.changed,
        added_lines=result.added_lines,
        removed_lines=result.removed_lines,
        unified_diff=result.unified_diff,
    )


@app.post("/enrich", response_model=EnrichmentRecord)
async def enrich(
    req: IngestRequest,
    store: VectorStore = Depends(get_store),
) -> EnrichmentRecord:
    """Extract structured markers (dates/metrics/error codes) and persist them.

    Automated Enrichment (use case #3): the markers become a fossil-layer-
    versioned structured record, retrievable via ``/markers``.
    """
    try:
        doc = extract_document(
            filename=req.filename,
            data=req.text.encode("utf-8"),
            content_type=req.content_type,
            source_id=req.source_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    markers = extract_markers(doc.text)
    record = await store.store_markers(
        doc_id=doc.doc_id,
        source_id=doc.source_id,
        layer_version=req.layer_version,
        markers=markers,
    )
    log.info(
        "event=enrich source_id=%s layer=%d markers=%d",
        doc.source_id,
        req.layer_version,
        len(markers),
    )
    return record


@app.get("/markers", response_model=EnrichmentRecord)
async def markers(
    source_id: str = Query(..., min_length=1),
    version: int = Query(1, ge=1),
    store: VectorStore = Depends(get_store),
) -> EnrichmentRecord:
    """Return the stored enrichment record for a document layer."""
    record = await store.get_markers(source_id, version)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"no enrichment for source_id {source_id!r} v{version}"
        )
    return record


@app.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    store: VectorStore = Depends(get_store),
    embedder: Embedder = Depends(get_embedder),
    llm: LLMProvider = Depends(get_llm),
    cache: PromptCache = Depends(get_prompt_cache),
) -> ChatResponse:
    """Chat-Based Fossil Excavation: answer the latest turn, grounded in fossils.

    Retrieves for the latest user message (optionally scoped to one document),
    answers via the LLM with the full conversation, and cites the fossils used
    (with geological age). Prompt Fossilization caches per (model, dialogue,
    retrieved fossils).
    """
    t0 = time.perf_counter()
    last_user = next((m.content for m in reversed(req.messages) if m.role == "user"), None)
    if last_user is None:
        raise HTTPException(status_code=422, detail="conversation must contain a user message")

    query_vec = embedder.encode_one(last_user)
    try:
        hits = await store.search(query_vec, req.k, req.source_id)
    except Exception:
        log.exception("event=chat_retrieval_failed")
        raise HTTPException(status_code=500, detail="retrieval failed") from None

    is_mock = llm.model_id.startswith("mock")
    convo = json.dumps([{"role": m.role, "content": m.content} for m in req.messages])
    key = fossil_key(llm.model_id, convo, "chat", hits)
    try:
        cached_answer = cache.get(key)
    except Exception:
        log.warning("event=chat_cache_get_failed")
        cached_answer = None
    if cached_answer is not None:
        answer, cached = cached_answer, True
    else:
        try:
            result = llm.chat(messages=req.messages, hits=hits)
        except Exception:
            log.exception("event=chat_llm_failed model=%s", llm.model_id)
            raise HTTPException(status_code=502, detail="LLM provider error") from None
        answer, cached = result.text, False
        with contextlib.suppress(Exception):
            cache.put(key, answer)

    latency_ms = (time.perf_counter() - t0) * 1000
    log.info(
        "event=chat turns=%d hits=%d model=%s cached=%s latency_ms=%.2f",
        len(req.messages),
        len(hits),
        llm.model_id,
        cached,
        latency_ms,
    )
    return ChatResponse(
        answer=answer,
        model_id=llm.model_id,
        mock=is_mock,
        cached=cached,
        citations=hits,
        latency_ms=latency_ms,
    )


@app.post("/slide/mutate", response_model=SlideMutateResponse)
async def slide_mutate(
    req: SlideMutateRequest,
    store: VectorStore = Depends(get_store),
    embedder: Embedder = Depends(get_embedder),
    llm: LLMProvider = Depends(get_llm),
    cache: PromptCache = Depends(get_prompt_cache),
) -> SlideMutateResponse:
    """PowerPoint Slide Mutator: propose an edit + show the diff, with versioning.

    Composes the LLM (edit), Fossil Diff (original vs suggestion), and — when
    ``persist`` + ``source_id`` are given — version tracking by saving the
    suggestion as a new fossil layer (then visible via ``/timetravel`` & ``/diff``).
    """
    is_mock = llm.model_id.startswith("mock")
    key = fossil_key(llm.model_id, req.text, f"edit:{req.instruction}", [])
    try:
        cached_suggestion = cache.get(key)
    except Exception:
        log.warning("event=slide_cache_get_failed")
        cached_suggestion = None
    if cached_suggestion is not None:
        suggestion, cached = cached_suggestion, True
    else:
        try:
            result = llm.edit(text=req.text, instruction=req.instruction)
        except Exception:
            log.exception("event=slide_mutate_llm_failed model=%s", llm.model_id)
            raise HTTPException(status_code=502, detail="LLM provider error") from None
        suggestion, cached = result.text, False
        with contextlib.suppress(Exception):
            cache.put(key, suggestion)

    diff = unified_text_diff(
        req.text.splitlines(),
        suggestion.splitlines(),
        from_label="original",
        to_label="suggestion",
    )

    persisted_version: int | None = None
    if req.persist and req.source_id:
        latest = await store.latest_layer(req.source_id)
        version = (latest or 0) + 1
        result = await ingest_document(
            store=store,
            embedder=embedder,
            filename=req.source_id,
            data=suggestion.encode("utf-8"),
            source_id=req.source_id,
            layer_version=version,
        )
        # Only advertise a version if something was actually indexed — an empty
        # suggestion produces zero chunks and must NOT report a phantom layer.
        persisted_version = version if result.chunks_indexed > 0 else None

    log.info(
        "event=slide_mutate source_id=%s changed=%s persisted=%s model=%s cached=%s",
        req.source_id,
        diff.changed,
        persisted_version,
        llm.model_id,
        cached,
    )
    return SlideMutateResponse(
        source_id=req.source_id,
        instruction=req.instruction,
        original=req.text,
        suggestion=suggestion,
        model_id=llm.model_id,
        mock=is_mock,
        cached=cached,
        changed=diff.changed,
        added_lines=diff.added_lines,
        removed_lines=diff.removed_lines,
        unified_diff=diff.unified_diff,
        persisted_version=persisted_version,
    )


@app.post("/dataset", response_model=DatasetResponse)
async def dataset(
    req: DatasetRequest,
    store: VectorStore = Depends(get_store),
) -> DatasetResponse:
    """Fine-Tuning Dataset Builder: JSONL instruction/response pairs from fossils."""
    version = req.version
    if version is None:
        version = await store.latest_layer(req.source_id)
    if version is None:
        raise HTTPException(status_code=404, detail=f"no fossil layers for {req.source_id!r}")
    chunks = await store.get_layer(req.source_id, version)
    if not chunks:
        raise HTTPException(status_code=404, detail=f"layer v{version} not found")
    try:
        records = build_pairs(chunks, fmt=req.fmt, templates=req.templates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    log.info(
        "event=dataset_built source_id=%s version=%d fmt=%s records=%d",
        req.source_id,
        version,
        req.fmt,
        len(records),
    )
    return DatasetResponse(
        source_id=req.source_id,
        version=version,
        format=req.fmt,
        count=len(records),
        records=records,
        jsonl=to_jsonl(records),
    )
