"""Centralised configuration via pydantic-settings (Pydantic v2).

Every tunable is an environment variable so the same image runs unchanged in
local compose, CI, and AWS. Settings are read once and cached
(:func:`get_settings`); handlers receive them through FastAPI's dependency
system rather than reading ``os.environ`` ad hoc.

Env vars use the ``FOSSILRAG_`` prefix (e.g. ``FOSSILRAG_EMBED_PROVIDER``).
``DATABASE_URL`` is the one exception — it is read both with and without the
prefix so the conventional unprefixed ``DATABASE_URL`` (used by the CI
Postgres service and most hosting platforms) works out of the box.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration."""

    model_config = SettingsConfigDict(
        env_prefix="FOSSILRAG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Datastore -------------------------------------------------------
    database_url: str = Field(
        default="postgresql://fossilrag:fossilrag@localhost:5432/fossilrag",
        validation_alias=AliasChoices("FOSSILRAG_DATABASE_URL", "DATABASE_URL"),
        description="asyncpg DSN for the Postgres+pgvector store.",
    )
    pool_min_size: int = 2
    pool_max_size: int = 10
    command_timeout_sec: float = 10.0

    # --- Embeddings ------------------------------------------------------
    # The active provider fixes the vector space. Indexes are keyed by
    # (embed_model, embed_dim) — the "fossil layer" principle — and are NEVER
    # cross-queried, because vectors from different models are incomparable
    # even at identical dimensionality. Switching providers => re-embed.
    embed_provider: str = Field(
        default="mock",
        description="Embedder backend: mock | local | bedrock.",
    )
    embed_model: str = Field(
        default="mock-deterministic-v1",
        description="Model identifier recorded against every vector for provenance.",
    )
    embed_dim: int = Field(
        default=384,
        ge=2,
        # pgvector's HNSW index on the `vector` type caps at 2000 dims, and
        # `bootstrap()` always builds an HNSW index — so this is the real
        # ceiling, not the 16000-dim raw-column limit. A model beyond 2000
        # dims (e.g. text-embedding-3-large at 3072) would need `halfvec`
        # (indexable to 4000); revisit this bound in the PR that adds it.
        le=2000,
        description=(
            "Embedding dimensionality. Default 384 matches the local "
            "sentence-transformers fallback (all-MiniLM-L6-v2); Bedrock Titan "
            "v2 uses 1024 and is provisioned as its own fossil layer. Capped "
            "at 2000 (pgvector HNSW index limit on the `vector` type)."
        ),
    )

    # --- Vector search ---------------------------------------------------
    vector_table: str = "fossil_chunks"
    # pgvector HNSW query-time recall knob. Must be >= the largest top-k you
    # serve; higher = better recall, slower. See pgvector 0.8 docs.
    hnsw_ef_search: int = 100

    # --- Service ---------------------------------------------------------
    log_level: str = "INFO"
    service_name: str = "fossilrag"

    @field_validator("pool_max_size")
    @classmethod
    def _max_ge_min(cls, v: int, info):  # noqa: ANN001
        min_size = info.data.get("pool_min_size", 0)
        if v <= 0:
            raise ValueError("pool_max_size must be positive")
        if v < min_size:
            raise ValueError(f"pool_max_size ({v}) must be >= pool_min_size ({min_size})")
        return v

    @field_validator("hnsw_ef_search")
    @classmethod
    def _ef_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("hnsw_ef_search must be >= 1")
        return v


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton (cached)."""
    return Settings()
