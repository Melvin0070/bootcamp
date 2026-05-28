"""AWS Bedrock embedder — Amazon Titan Text Embeddings V2 (the cloud default).

Model ``amazon.titan-embed-text-v2:0`` with a configurable output dimension
(1024 default / 512 / 256) and ``normalize=true``. Titan embeds one ``inputText``
per call, so :meth:`encode` issues one ``invoke_model`` per text.

``boto3`` is the optional ``aws`` extra, imported lazily; the client is
**injectable** (``client=``) so request-building and response-parsing are
unit-tested against a tiny fake — no real Bedrock, no cost, no keys. (Bedrock
isn't emulable at $0 — LocalStack gates it behind the Ultimate tier — so a fake
client is the right test seam.)
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from fossilrag.logging import get_logger

log = get_logger("embedding.bedrock")

DEFAULT_MODEL = "amazon.titan-embed-text-v2:0"


class BedrockEmbedder:
    """Titan v2 embedder via the bedrock-runtime InvokeModel API."""

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL,
        *,
        dimensions: int = 1024,
        region: str = "us-east-1",
        client: Any = None,
    ) -> None:
        if dimensions not in (256, 512, 1024):
            raise ValueError(f"Titan v2 supports dimensions 256|512|1024, got {dimensions}")
        self._model_id = model_id
        self._dim = int(dimensions)
        self._region = region
        self._client = client

    def _ensure_client(self):  # noqa: ANN202
        if self._client is None:
            import boto3  # lazy, optional

            self._client = boto3.client("bedrock-runtime", region_name=self._region)
        return self._client

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dim

    def encode_one(self, text: str) -> np.ndarray:
        client = self._ensure_client()
        resp = client.invoke_model(
            modelId=self._model_id,
            accept="application/json",
            contentType="application/json",
            body=json.dumps(
                {
                    "inputText": text,
                    "dimensions": self._dim,
                    "normalize": True,
                    "embeddingTypes": ["float"],
                }
            ),
        )
        body = json.loads(resp["body"].read())
        vec = np.asarray(body["embedding"], dtype=np.float32)
        if vec.shape[0] != self._dim:
            raise ValueError(f"Titan returned dim {vec.shape[0]}, expected {self._dim}")
        return vec

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self._dim), dtype=np.float32)
        return np.vstack([self.encode_one(t) for t in texts])
