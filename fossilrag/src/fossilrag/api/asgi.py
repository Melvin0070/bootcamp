"""AWS Lambda entrypoint for the FastAPI app via Mangum (ASGI → Lambda).

Used by the API Lambda behind API Gateway HTTP API (see infra/lambda.tf,
``handler = fossilrag.api.asgi.handler``). ``mangum`` is the optional ``aws``
extra; this module is only imported by the Lambda runtime (and its test), never
by the package, so the core install needn't ship it.
"""

from __future__ import annotations

from mangum import Mangum

from fossilrag.api.app import app

# API Gateway HTTP API uses payload format 2.0 (Mangum's default).
handler = Mangum(app)
