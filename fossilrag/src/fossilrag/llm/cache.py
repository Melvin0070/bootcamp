"""Prompt Fossilization — cache successful prompt→output pairs for instant reuse.

The mutation the brief calls "Prompt Fossilization": a repeated ``/mutate`` over
the same query+corpus rebuilds the same grounded prompt, so we key a cache on
``(model_id, query, instruction, retrieved chunk_ids)`` and serve the stored
output instantly on a hit — no LLM call, no cost, no latency.

Three backends behind one :class:`PromptCache` Protocol: in-memory (default,
per-process), local-file JSON (dev persistence), and DynamoDB (shared, with TTL).
``boto3`` is lazy/optional.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from fossilrag.logging import get_logger
from fossilrag.models import ExcavateHit

log = get_logger("llm.cache")


def fossil_key(model_id: str, query: str, instruction: str | None, hits: list[ExcavateHit]) -> str:
    """Stable, injective cache key over everything that shapes the LLM prompt.

    Includes each hit's ``(chunk_id, layer_version, geological_age)`` — not just
    ``chunk_id`` — because the prompt renders the geological age, and re-ingesting
    a document at a new fossil-layer version can change a chunk's age in place.
    Keying on chunk_id alone would serve a stale (wrong-age) cached summary.
    The JSON structure (length-disambiguated) makes the key injective, so
    distinct inputs can't collide via delimiter games.
    """
    payload = json.dumps(
        {
            "model": model_id,
            "query": query,
            "instruction": instruction or "",  # None and "" yield the same prompt
            "hits": [[h.chunk_id, h.layer_version, h.geological_age] for h in hits],
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@runtime_checkable
class PromptCache(Protocol):
    def get(self, key: str) -> str | None: ...
    def put(self, key: str, value: str) -> None: ...
    def stats(self) -> dict[str, int]: ...


class InMemoryPromptCache:
    """Per-process LRU cache (bounded, so a long-lived server can't leak)."""

    def __init__(self, max_entries: int = 4096) -> None:
        self._data: OrderedDict[str, str] = OrderedDict()
        self._max = max_entries
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)  # mark most-recently-used
            return self._data[key]

    def put(self, key: str, value: str) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)  # evict least-recently-used

    def stats(self) -> dict[str, int]:
        return {"entries": len(self._data), "max_entries": self._max}


class LocalFilePromptCache:
    """JSON-file cache for dev persistence. SINGLE-PROCESS only (the in-process
    lock doesn't serialise across processes/uvicorn workers); use the DynamoDB
    backend for any multi-worker deployment.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._write({})

    def _read(self) -> dict[str, str]:
        with self.path.open(encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: dict[str, str]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f)
        tmp.replace(self.path)

    def get(self, key: str) -> str | None:
        return self._read().get(key)

    def put(self, key: str, value: str) -> None:
        with self._lock:
            data = self._read()
            data[key] = value
            self._write(data)

    def stats(self) -> dict[str, int]:
        return {"entries": len(self._read())}


class DynamoDBPromptCache:
    """DynamoDB-backed cache. Schema: ``key`` (S, PK), ``value`` (S), ``ttl`` (N)."""

    def __init__(
        self,
        table_name: str,
        *,
        region: str | None = None,
        client: Any = None,
        ttl_sec: int = 604800,
    ) -> None:
        self.table_name = table_name
        self.ttl_sec = ttl_sec
        if client is not None:
            self._client = client
        else:
            import boto3  # lazy, optional

            self._client = boto3.client(
                "dynamodb", region_name=region or os.environ.get("AWS_REGION", "us-east-1")
            )

    def get(self, key: str) -> str | None:
        resp = self._client.get_item(TableName=self.table_name, Key={"key": {"S": key}})
        item = resp.get("Item")
        return item["value"]["S"] if item else None

    def put(self, key: str, value: str) -> None:
        item = {"key": {"S": key}, "value": {"S": value}}
        if self.ttl_sec:
            item["ttl"] = {"N": str(int(time.time()) + self.ttl_sec)}
        self._client.put_item(TableName=self.table_name, Item=item)

    def stats(self) -> dict[str, int]:
        with contextlib.suppress(Exception):
            return {
                "item_count": int(
                    self._client.describe_table(TableName=self.table_name)["Table"]["ItemCount"]
                )
            }
        return {}


def make_prompt_cache(settings=None) -> PromptCache:  # noqa: ANN001
    """Construct the cache named by ``prompt_cache_backend`` (memory|local|dynamodb)."""
    from fossilrag.config import get_settings

    settings = settings or get_settings()
    backend = settings.prompt_cache_backend.lower()
    if backend == "memory":
        return InMemoryPromptCache()
    if backend == "local":
        return LocalFilePromptCache(settings.prompt_cache_path)
    if backend == "dynamodb":
        if not settings.prompt_cache_table:
            raise ValueError("prompt_cache_backend=dynamodb requires FOSSILRAG_PROMPT_CACHE_TABLE")
        return DynamoDBPromptCache(settings.prompt_cache_table, region=settings.aws_region)
    raise ValueError(f"unknown prompt_cache_backend {backend!r} (memory|local|dynamodb)")
