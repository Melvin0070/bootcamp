"""FastAPI app: ``/excavate`` (top-k retrieval) + ``/ingest`` (spine demo).

Lifespan wiring follows the FastAPI 0.136 + house pattern: the embedder and
the connected/bootstrapped vector store are built once at startup, stored on
``app.state``, and reached in handlers via ``Depends`` providers — never a
per-request handshake. ``/mutate`` and the mutation endpoints (time-travel,
diff, dataset) are layered on in later PRs without disturbing this surface.
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
from fossilrag.logging import configure_logging, get_logger
from fossilrag.models import ExcavateResponse, IngestResult
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


# -- request models -------------------------------------------------------


class IngestRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=512)
    text: str = Field(min_length=1)
    content_type: str = "text/plain"
    user_id: str | None = None
    layer_version: int = Field(default=1, ge=1)


# -- endpoints ------------------------------------------------------------


@app.get("/")
async def root(settings: Settings = Depends(settings_dep)) -> dict:
    return {
        "service": settings.service_name,
        "version": __version__,
        "embed_model": settings.embed_model,
        "embed_dim": settings.embed_dim,
        "vector_backend": "pgvector",
        "endpoints": ["/excavate", "/ingest", "/healthz"],
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
