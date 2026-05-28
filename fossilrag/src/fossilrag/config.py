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
    pool_min_size: int = Field(default=2, ge=0)
    pool_max_size: int = 10
    command_timeout_sec: float = Field(default=10.0, gt=0)

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

    # --- Chunking --------------------------------------------------------
    chunk_max_tokens: int = Field(default=256, ge=16)
    chunk_overlap_tokens: int = Field(default=32, ge=0)
    gold_format: str = "jsonl"  # jsonl | parquet

    # --- Vector search ---------------------------------------------------
    vector_table: str = "fossil_chunks"
    # pgvector HNSW query-time recall knob. Must be >= the largest top-k you
    # serve; higher = better recall, slower. See pgvector 0.8 docs.
    hnsw_ef_search: int = 100

    # --- Ingestion / AWS -------------------------------------------------
    aws_region: str = "us-east-1"
    # Override the AWS endpoint for LocalStack, e.g. http://localhost:4566.
    # Read unprefixed too, since boto3 honours AWS_ENDPOINT_URL natively.
    aws_endpoint_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FOSSILRAG_AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL"),
    )
    silver_bucket: str | None = None
    silver_prefix: str = "silver"

    # --- SQS worker (Auto-Scaling Lambda + DLQ mutation) -----------------
    sqs_max_attempts: int = Field(default=3, ge=1)
    sqs_base_delay: float = Field(default=0.1, ge=0.0)  # 0 disables backoff sleeps

    # --- Idempotency (Self-Healing Idempotency mutation) -----------------
    idempotency_backend: str = "null"  # null | local | dynamodb
    idempotency_table: str | None = None
    idempotency_manifest_path: str = "./.fossilrag/idempotency.json"

    # --- LLM / mutate ----------------------------------------------------
    llm_provider: str = "mock"  # mock | bedrock | anthropic
    # Bedrock cross-region inference profile id (not the bare foundation id).
    llm_model: str = "us.anthropic.claude-sonnet-4-6"
    anthropic_model: str = "claude-sonnet-4-6"
    llm_max_tokens: int = Field(default=1024, ge=1)
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    bedrock_prompt_cache: bool = True  # Bedrock native cachePoint on the system prefix

    # --- Prompt Fossilization (prompt/output cache mutation) -------------
    prompt_cache_backend: str = "memory"  # memory | local | dynamodb
    prompt_cache_table: str | None = None
    prompt_cache_path: str = "./.fossilrag/prompt_cache.json"

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

    @field_validator("chunk_overlap_tokens")
    @classmethod
    def _overlap_lt_max(cls, v: int, info):  # noqa: ANN001
        mx = info.data.get("chunk_max_tokens")
        if mx is not None and v >= mx:
            raise ValueError(f"chunk_overlap_tokens ({v}) must be < chunk_max_tokens ({mx})")
        return v

    @field_validator("gold_format")
    @classmethod
    def _gold_format_known(cls, v: str) -> str:
        if v not in {"jsonl", "parquet"}:
            raise ValueError(f"gold_format must be 'jsonl' or 'parquet' (got {v!r})")
        return v

    @field_validator("idempotency_backend")
    @classmethod
    def _idem_backend_known(cls, v: str) -> str:
        if v not in {"null", "local", "dynamodb"}:
            raise ValueError(f"idempotency_backend must be null|local|dynamodb (got {v!r})")
        return v

    @field_validator("llm_provider")
    @classmethod
    def _llm_provider_known(cls, v: str) -> str:
        if v not in {"mock", "bedrock", "anthropic"}:
            raise ValueError(f"llm_provider must be mock|bedrock|anthropic (got {v!r})")
        return v

    @field_validator("prompt_cache_backend")
    @classmethod
    def _prompt_cache_backend_known(cls, v: str) -> str:
        if v not in {"memory", "local", "dynamodb"}:
            raise ValueError(f"prompt_cache_backend must be memory|local|dynamodb (got {v!r})")
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
