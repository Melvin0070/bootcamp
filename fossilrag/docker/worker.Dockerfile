# FossilRAG ingestion-worker image (compose `aws` profile).
# Same codebase as the API image, but installs the `aws` extra (boto3) and
# ships the scripts/ entrypoints — one image runs the bootstrap one-shot
# (scripts.localstack_init), the long-poll worker (scripts.worker), and the
# demo driver (scripts.demo_aws). Not a Lambda artifact; in AWS the worker
# runs from the deployment zip via the event-source mapping (see infra/).
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Deps first for layer caching, then source + the script entrypoints.
COPY pyproject.toml README.md ./
COPY src ./src
# aws = boto3/SQS/S3/DynamoDB; openai = the embedder for indexed mode
# (worker_index=true → extract→chunk→embed→upsert into pgvector).
RUN pip install --upgrade pip && pip install ".[aws,openai]"
COPY scripts ./scripts

# Drop privileges.
RUN useradd --create-home --uid 10001 fossil && chown -R fossil /app
USER fossil

# Default to the long-poll worker; the bootstrap service overrides `command`.
CMD ["python", "-m", "scripts.worker"]
