"""Embedding sinks.

A sink takes (chunk_id, vector, chunk_metadata) and persists the vector,
returning a URI string that the idempotency store records against the
chunk_id. Two implementations:

  * LocalSink  — writes to {dir}/{chunk_id}.npy. For dev / tests.
  * S3Sink     — writes to s3://{bucket}/{prefix}/{chunk_id}.npy.

The chunk_id is the filename so the sink itself is idempotent at the storage
layer: writing the same vector under the same id is a no-op of last-write-
wins. Combined with content-addressed ids, this means even if the
idempotency store gets corrupted, replaying the pipeline never produces
duplicate embeddings — the *file* would just be overwritten by an identical
copy.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any, Mapping, Protocol

import numpy as np

log = logging.getLogger("embedding_pipeline.sink")


class EmbeddingSink(Protocol):
    def write(self, chunk_id: str, vector: np.ndarray, chunk: Mapping[str, Any]) -> str:
        """Persist the vector and return a URI string identifying it."""


class LocalSink:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, chunk_id: str, vector: np.ndarray, chunk: Mapping[str, Any]) -> str:
        out = self.output_dir / f"{chunk_id}.npy"
        # Atomic write: temp file + os.replace so a crash never leaves a
        # partial .npy behind. We write through a file handle so np.save
        # doesn't auto-append `.npy` to a path that already ends in .tmp.
        tmp = out.with_suffix(out.suffix + ".tmp")
        with tmp.open("wb") as f:
            np.save(f, vector.astype("float32"))
        tmp.replace(out)
        log.debug("event=sink_wrote uri=%s bytes=%d", out, vector.nbytes)
        return str(out)


class S3Sink:
    def __init__(
        self,
        bucket: str,
        prefix: str = "embeddings/",
        *,
        client: Any = None,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/"
        if client is not None:
            self._client = client
        else:
            import boto3
            self._client = boto3.client("s3")

    def write(self, chunk_id: str, vector: np.ndarray, chunk: Mapping[str, Any]) -> str:
        key = f"{self.prefix}{chunk_id}.npy"
        buf = io.BytesIO()
        np.save(buf, vector.astype("float32"))
        buf.seek(0)
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=buf.getvalue(),
            ContentType="application/octet-stream",
            Metadata={
                "source": str(chunk.get("source", "")),
                "chunk_index": str(chunk.get("chunk_index", "")),
            },
        )
        uri = f"s3://{self.bucket}/{key}"
        log.debug("event=sink_wrote uri=%s bytes=%d", uri, vector.nbytes)
        return uri
