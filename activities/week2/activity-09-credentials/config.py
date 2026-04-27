"""
Application config — production-grade.

Differences from `broken/config.py`:

  1. Zero secrets in source. Every secret is resolved at runtime via
     `get_secret(...)` — env var first, then AWS Secrets Manager.
  2. boto3 clients are constructed with NO static credentials; they pick
     up the default provider chain (IAM role on EC2/ECS/EKS/Lambda).
  3. The OpenAI client is constructed lazily (`get_openai_client`) so we
     never resolve the API key during a `python -c "import config"` smoke
     test or a unit-test import.
  4. Nothing is printed at module import time. The bootstrap log line that
     used to leak the key is gone.
  5. Non-secret config (region, bucket name) still uses `os.environ.get(...)`
     because there's no point paying Secrets Manager cost for a public ARN
     fragment.

If you find yourself adding `aws_access_key_id=...` or an `OPENAI_API_KEY`
literal to this file, stop. The whole point of this module is that nothing
sensitive ever lives at rest in the repo.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

import boto3

from secrets_manager import get_secret

log = logging.getLogger("config")

# Non-secret runtime config — safe to live in env vars on every platform.
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
S3_BUCKET = os.environ.get("S3_BUCKET", "fossilrag-prod")
SECRETS_PREFIX = os.environ.get("SECRETS_PREFIX", "fossilrag/prod")


@lru_cache(maxsize=1)
def get_s3_client():
    """Return a boto3 S3 client using the default credential provider chain.

    No explicit credentials. In prod the IAM role attached to the task /
    instance / Lambda provides the creds. In local dev `~/.aws/credentials`
    or env vars do. Either way, this function is the SINGLE place that
    constructs the client, so a future swap to a different provider chain
    or to Localstack is a one-line change.
    """
    log.info("event=s3_client_init region=%s", AWS_REGION)
    return boto3.client("s3", region_name=AWS_REGION)


@lru_cache(maxsize=1)
def get_openai_client():
    """Construct the OpenAI client lazily so the key isn't resolved on import."""
    import openai
    api_key = get_secret("OPENAI_API_KEY")
    log.info("event=openai_client_init")
    return openai.OpenAI(api_key=api_key)


def get_database_url() -> str:
    """Return the DATABASE_URL secret, refreshed every TTL."""
    return get_secret("DATABASE_URL")
