# FossilRAG retrieval API image.
# Slim, non-root, no build toolchain — asyncpg/numpy ship manylinux wheels for
# cp312, so the image stays small and Lambda-container-friendly.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps first (better layer caching): copy only what the build backend
# needs to resolve dependencies, then the source.
COPY pyproject.toml README.md ./
COPY src ./src
# `.[openai]` so the real-AI demo path (OpenAI / Gemini OpenAI-compat) works by
# env; the mock default needs no key. Bedrock would additionally need `.[aws]`.
RUN pip install --upgrade pip && pip install ".[openai]"

# Drop privileges.
RUN useradd --create-home --uid 10001 fossil && chown -R fossil /app
USER fossil

EXPOSE 8000

# Liveness: the root endpoint needs no DB, so it flips healthy as soon as the
# app has started (DB readiness is gated by compose depends_on + the lifespan,
# which fails startup if Postgres is unreachable).
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=12 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/').read()"]

CMD ["uvicorn", "fossilrag.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
