"""Self-Healing Idempotency ledger — skip already-indexed chunks.

The mutation the brief calls "Self-Healing Idempotency": before paying for an
embedding (Bedrock costs per call), check a ledger; if a chunk is already
embedded for the active ``(model_id, dim)`` vector space, skip it. A crash
between claim and commit leaves a reclaimable PENDING record, so failed work is
retried — never silently lost.

Four backends behind one :class:`IdempotencyStore` Protocol:

  * **NullStore** — default no-op (nothing is ever "processed"); the vector
    store's ``ON CONFLICT`` upsert already makes re-indexing *correct*, so this
    is the safe default when you don't need the cost-saving skip.
  * **LocalManifestStore** — JSON file; dev/single-process.
  * **DynamoDBStore** — production; atomic claims via ``ConditionExpression``
    so concurrent workers can share a batch; TTL reclaims dead PENDING records.

Adapted from the activity-08 idempotency pattern; keyed here by the full
``model_id:dim:chunk_id`` so two embedding models never share a ledger entry.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from fossilrag.logging import get_logger

log = get_logger("idempotency")


@runtime_checkable
class IdempotencyStore(Protocol):
    def is_processed(self, key: str) -> bool: ...
    def claim(self, key: str) -> bool: ...
    def mark_processed(self, key: str, ref: str) -> None: ...
    def release(self, key: str) -> None: ...
    def stats(self) -> dict[str, int]: ...


class NullStore:
    """No-op ledger: nothing is ever processed, every claim succeeds.

    Re-embeds every time, relying on the vector store's idempotent upsert for
    correctness. The safe default; swap to a real backend to save embedding cost.
    """

    def is_processed(self, key: str) -> bool:
        return False

    def claim(self, key: str) -> bool:
        return True

    def mark_processed(self, key: str, ref: str) -> None:
        pass

    def release(self, key: str) -> None:
        pass

    def stats(self) -> dict[str, int]:
        return {"processed": 0, "pending": 0}


class LocalManifestStore:
    """JSON-file manifest with an in-process lock. Dev / single-process only."""

    PENDING_TTL_SEC = 3600

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._write({})

    def _read(self) -> dict[str, dict[str, Any]]:
        with self.path.open(encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True)
        os.replace(tmp, self.path)

    def is_processed(self, key: str) -> bool:
        rec = self._read().get(key)
        return rec is not None and rec.get("status") == "PROCESSED"

    def claim(self, key: str) -> bool:
        with self._lock:
            data = self._read()
            rec = data.get(key)
            if rec is not None:
                if rec.get("status") == "PROCESSED":
                    return False
                if time.time() - rec.get("claimed_at", 0) < self.PENDING_TTL_SEC:
                    return False
            data[key] = {"status": "PENDING", "claimed_at": time.time()}
            self._write(data)
        return True

    def mark_processed(self, key: str, ref: str) -> None:
        with self._lock:
            data = self._read()
            data[key] = {"status": "PROCESSED", "ref": ref, "processed_at": time.time()}
            self._write(data)

    def release(self, key: str) -> None:
        with self._lock:
            data = self._read()
            if data.get(key, {}).get("status") == "PENDING":
                del data[key]
                self._write(data)

    def stats(self) -> dict[str, int]:
        data = self._read()
        return {
            "processed": sum(1 for r in data.values() if r.get("status") == "PROCESSED"),
            "pending": sum(1 for r in data.values() if r.get("status") == "PENDING"),
        }


class DynamoDBStore:
    """DynamoDB ledger. Atomic conditional claims; TTL reclaims stale PENDINGs.

    Schema: ``key`` (S, PK), ``status`` (S), ``ref`` (S), ``claimed_at`` (N),
    ``ttl`` (N, DynamoDB TTL). ``claim`` is a conditional PutItem that succeeds
    iff no record exists OR an existing PENDING claim's TTL has expired — so two
    concurrent workers race and exactly one wins.
    """

    def __init__(
        self,
        table_name: str,
        *,
        region: str | None = None,
        client: Any = None,
        pending_ttl_sec: int = 3600,
    ) -> None:
        self.table_name = table_name
        self.pending_ttl_sec = pending_ttl_sec
        if client is not None:
            self._client = client
        else:
            import boto3

            self._client = boto3.client(
                "dynamodb", region_name=region or os.environ.get("AWS_REGION", "us-east-1")
            )

    def is_processed(self, key: str) -> bool:
        resp = self._client.get_item(
            TableName=self.table_name,
            Key={"key": {"S": key}},
            ConsistentRead=True,
            ProjectionExpression="#s",
            ExpressionAttributeNames={"#s": "status"},
        )
        item = resp.get("Item")
        return item is not None and item.get("status", {}).get("S") == "PROCESSED"

    def claim(self, key: str) -> bool:
        now = int(time.time())
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item={
                    "key": {"S": key},
                    "status": {"S": "PENDING"},
                    "claimed_at": {"N": str(now)},
                    "ttl": {"N": str(now + self.pending_ttl_sec)},
                },
                ConditionExpression="attribute_not_exists(#k) OR (#s = :pending AND #ttl < :now)",
                ExpressionAttributeNames={"#k": "key", "#s": "status", "#ttl": "ttl"},
                ExpressionAttributeValues={":pending": {"S": "PENDING"}, ":now": {"N": str(now)}},
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    def mark_processed(self, key: str, ref: str) -> None:
        now = int(time.time())
        self._client.update_item(
            TableName=self.table_name,
            Key={"key": {"S": key}},
            UpdateExpression="SET #s = :p, #r = :ref, processed_at = :now REMOVE #ttl, claimed_at",
            ExpressionAttributeNames={"#s": "status", "#ttl": "ttl", "#r": "ref"},
            ExpressionAttributeValues={
                ":p": {"S": "PROCESSED"},
                ":ref": {"S": ref},
                ":now": {"N": str(now)},
            },
        )

    def release(self, key: str) -> None:
        with contextlib.suppress(self._client.exceptions.ConditionalCheckFailedException):
            self._client.delete_item(
                TableName=self.table_name,
                Key={"key": {"S": key}},
                ConditionExpression="#s = :pending",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":pending": {"S": "PENDING"}},
            )

    def stats(self) -> dict[str, int]:
        processed = pending = 0
        paginator = self._client.get_paginator("scan")
        for page in paginator.paginate(
            TableName=self.table_name,
            ProjectionExpression="#s",
            ExpressionAttributeNames={"#s": "status"},
        ):
            for item in page.get("Items", []):
                s = item.get("status", {}).get("S")
                processed += s == "PROCESSED"
                pending += s == "PENDING"
        return {"processed": processed, "pending": pending}


def make_idempotency_store(settings=None) -> IdempotencyStore:  # noqa: ANN001
    """Construct the ledger named by ``idempotency_backend`` (null|local|dynamodb)."""
    from fossilrag.config import get_settings

    settings = settings or get_settings()
    backend = settings.idempotency_backend.lower()
    if backend == "null":
        return NullStore()
    if backend == "local":
        return LocalManifestStore(settings.idempotency_manifest_path)
    if backend == "dynamodb":
        if not settings.idempotency_table:
            raise ValueError("idempotency_backend=dynamodb requires FOSSILRAG_IDEMPOTENCY_TABLE")
        return DynamoDBStore(settings.idempotency_table, region=settings.aws_region)
    raise ValueError(f"unknown idempotency_backend {backend!r} (null|local|dynamodb)")
