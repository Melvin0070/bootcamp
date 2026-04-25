"""Idempotency stores for the embedding pipeline.

Three backends sit behind a single `IdempotencyStore` Protocol:

  * DynamoDBStore — production. Atomic claims via ConditionExpression so
    multiple workers can run the same batch concurrently; TTL on PENDING
    records reclaims work that died mid-flight.
  * S3ManifestStore — simpler, single-writer. Suitable for nightly batch
    jobs where atomic concurrent claims aren't needed.
  * LocalManifestStore — JSON file on disk. For dev and tests.

State machine:
    (none) ──claim()──▶ PENDING ──mark_processed()──▶ PROCESSED
                          │
                          ├──release()──▶ (none)
                          │
                          └──TTL expiry─▶ (none)         (DynamoDB only)

The flow guarantees:
    1. is_processed(id) is true ⇔ a successful embedding exists at the URI
       recorded against id. Workers can short-circuit cheaply.
    2. claim(id) is atomic — only one worker can claim a given id at a time.
    3. A crash between claim() and mark_processed() leaves a PENDING record;
       the next run re-claims it (DynamoDB via TTL, S3/local via the heuristic
       in `is_stale`).

Why not "write the embedding first, then write to DDB"?
    It eliminates the orphan-DDB-record case but introduces an orphan-S3-blob
    case if DDB write fails. We chose claim-first because:
      - DDB write failures are rare and surface as exceptions; orphan S3 blobs
        would silently consume storage forever.
      - Embedding API calls are the expensive step; we want a *cheap claim*
        before paying for the embedding so a duplicate-claim race is detected
        before the API call, not after.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger("embedding_pipeline.idempotency")


# ===========================================================================
# Protocol — what every backend must implement
# ===========================================================================


class IdempotencyStore(Protocol):
    """Atomic claim / commit interface for chunk processing."""

    def is_processed(self, chunk_id: str) -> bool:
        """True iff the chunk has been successfully embedded and recorded."""

    def claim(self, chunk_id: str) -> bool:
        """Try to atomically claim the chunk. Returns False if another worker
        already holds the claim or the chunk is already processed."""

    def mark_processed(self, chunk_id: str, embedding_uri: str) -> None:
        """Commit the chunk → embedding mapping. Releases the claim."""

    def release(self, chunk_id: str) -> None:
        """Drop a claim without committing — used after a failure so the next
        run picks the chunk up cleanly."""

    def stats(self) -> dict[str, int]:
        """Return {processed, pending} counts for observability."""


# ===========================================================================
# LocalManifestStore — JSON file, in-process lock. For dev and tests.
# ===========================================================================


class LocalManifestStore:
    """JSON-file-backed manifest with an in-process lock.

    Concurrency model: a single Python process with multiple threads. The
    file is the source of truth between runs; the in-process lock serialises
    writes within a run. Cross-process concurrency is NOT supported — use
    DynamoDBStore for that.
    """

    PENDING_TTL_SEC = 3600  # claims older than this are reclaimable

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._write({})

    def _read(self) -> dict[str, dict[str, Any]]:
        with self.path.open(encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        # Atomic write via os.replace so a crash mid-write never leaves a
        # half-written manifest behind.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True, indent=2)
        os.replace(tmp, self.path)

    def is_processed(self, chunk_id: str) -> bool:
        data = self._read()
        rec = data.get(chunk_id)
        return rec is not None and rec.get("status") == "PROCESSED"

    def claim(self, chunk_id: str) -> bool:
        with self._lock:
            data = self._read()
            existing = data.get(chunk_id)
            if existing is not None:
                if existing.get("status") == "PROCESSED":
                    return False
                # Existing PENDING: reclaimable iff the TTL has expired.
                claimed_at = existing.get("claimed_at", 0)
                if time.time() - claimed_at < self.PENDING_TTL_SEC:
                    return False
            data[chunk_id] = {"status": "PENDING", "claimed_at": time.time()}
            self._write(data)
        log.info("event=chunk_claimed chunk_id=%s backend=local", chunk_id[:12])
        return True

    def mark_processed(self, chunk_id: str, embedding_uri: str) -> None:
        with self._lock:
            data = self._read()
            data[chunk_id] = {
                "status": "PROCESSED",
                "embedding_uri": embedding_uri,
                "processed_at": time.time(),
            }
            self._write(data)
        log.info(
            "event=chunk_processed chunk_id=%s uri=%s backend=local",
            chunk_id[:12], embedding_uri,
        )

    def release(self, chunk_id: str) -> None:
        with self._lock:
            data = self._read()
            existing = data.get(chunk_id)
            if existing is not None and existing.get("status") == "PENDING":
                del data[chunk_id]
                self._write(data)
        log.info("event=chunk_released chunk_id=%s backend=local", chunk_id[:12])

    def stats(self) -> dict[str, int]:
        data = self._read()
        return {
            "processed": sum(1 for r in data.values() if r.get("status") == "PROCESSED"),
            "pending": sum(1 for r in data.values() if r.get("status") == "PENDING"),
        }


# ===========================================================================
# DynamoDBStore — production. Atomic conditional writes; TTL reclaims stale.
# ===========================================================================


class DynamoDBStore:
    """DynamoDB-backed idempotency store.

    Schema:
        chunk_id (S, partition key)
        status: "PENDING" | "PROCESSED"
        embedding_uri: S (set on PROCESSED)
        claimed_at: N (epoch seconds, set on PENDING)
        ttl: N (DynamoDB TTL field, set on PENDING; auto-deletes after expiry)

    Atomicity:
        claim() is a PutItem with
            ConditionExpression="attribute_not_exists(chunk_id) OR ttl < :now"
        — succeeds iff no record exists OR an existing PENDING claim has
        already expired. Two concurrent workers can race; exactly one wins.

    Cost (us-east-1, on-demand):
        Read   $0.25/M     → is_processed across 50k chunks: ~$0.013
        Write  $1.25/M     → claim + mark_processed across 50k: ~$0.125
        Storage  $0.25/GB  → 50k records × ~200 B ≈ $0.0025/mo
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
                "dynamodb",
                region_name=region or os.environ.get("AWS_REGION", "us-east-1"),
            )

    def is_processed(self, chunk_id: str) -> bool:
        resp = self._client.get_item(
            TableName=self.table_name,
            Key={"chunk_id": {"S": chunk_id}},
            ConsistentRead=True,
            ProjectionExpression="#s",
            ExpressionAttributeNames={"#s": "status"},
        )
        item = resp.get("Item")
        return item is not None and item.get("status", {}).get("S") == "PROCESSED"

    def claim(self, chunk_id: str) -> bool:
        now = int(time.time())
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item={
                    "chunk_id": {"S": chunk_id},
                    "status": {"S": "PENDING"},
                    "claimed_at": {"N": str(now)},
                    "ttl": {"N": str(now + self.pending_ttl_sec)},
                },
                # Atomic claim: succeed iff no record OR existing TTL expired.
                ConditionExpression=(
                    "attribute_not_exists(chunk_id) "
                    "OR (#s = :pending AND #ttl < :now)"
                ),
                ExpressionAttributeNames={"#s": "status", "#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":pending": {"S": "PENDING"},
                    ":now": {"N": str(now)},
                },
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            return False
        log.info("event=chunk_claimed chunk_id=%s backend=dynamodb", chunk_id[:12])
        return True

    def mark_processed(self, chunk_id: str, embedding_uri: str) -> None:
        now = int(time.time())
        self._client.update_item(
            TableName=self.table_name,
            Key={"chunk_id": {"S": chunk_id}},
            UpdateExpression="SET #s = :p, embedding_uri = :uri, processed_at = :now REMOVE #ttl, claimed_at",
            ExpressionAttributeNames={"#s": "status", "#ttl": "ttl"},
            ExpressionAttributeValues={
                ":p": {"S": "PROCESSED"},
                ":uri": {"S": embedding_uri},
                ":now": {"N": str(now)},
            },
        )
        log.info(
            "event=chunk_processed chunk_id=%s uri=%s backend=dynamodb",
            chunk_id[:12], embedding_uri,
        )

    def release(self, chunk_id: str) -> None:
        # Only delete if still PENDING — never clobber a concurrent
        # mark_processed that won the race.
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={"chunk_id": {"S": chunk_id}},
                ConditionExpression="#s = :pending",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":pending": {"S": "PENDING"}},
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            pass
        log.info("event=chunk_released chunk_id=%s backend=dynamodb", chunk_id[:12])

    def stats(self) -> dict[str, int]:
        # Scan is fine for a small table or for ops dashboards. For a hot
        # path, add a GSI on status and Query instead.
        processed = pending = 0
        paginator = self._client.get_paginator("scan")
        for page in paginator.paginate(
            TableName=self.table_name,
            ProjectionExpression="#s",
            ExpressionAttributeNames={"#s": "status"},
        ):
            for item in page.get("Items", []):
                s = item.get("status", {}).get("S")
                if s == "PROCESSED":
                    processed += 1
                elif s == "PENDING":
                    pending += 1
        return {"processed": processed, "pending": pending}


# ===========================================================================
# S3ManifestStore — JSON manifest in S3. Single-writer assumption.
# ===========================================================================


class S3ManifestStore:
    """S3-backed manifest. Single-writer batch jobs only.

    The manifest is one JSON object at s3://{bucket}/{key}. Reads are
    eventually-consistent S3 GETs; writes are full PUTs of the manifest.
    There's no atomic compare-and-swap, so concurrent writers can clobber
    each other. Use DynamoDBStore for any multi-worker scenario.

    Suitable for: nightly cron, single-Lambda batch, dev environments
    without DynamoDB. The interface is identical to DynamoDBStore so the
    pipeline orchestrator doesn't care which one it gets.
    """

    PENDING_TTL_SEC = 3600

    def __init__(
        self,
        bucket: str,
        key: str = "embeddings/manifest.json",
        *,
        client: Any = None,
    ) -> None:
        self.bucket = bucket
        self.key = key
        if client is not None:
            self._client = client
        else:
            import boto3
            self._client = boto3.client("s3")
        self._cache: dict[str, dict[str, Any]] | None = None
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._cache is not None:
            return self._cache
        try:
            resp = self._client.get_object(Bucket=self.bucket, Key=self.key)
            self._cache = json.loads(resp["Body"].read())
        except self._client.exceptions.NoSuchKey:
            self._cache = {}
        return self._cache

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        body = json.dumps(data, sort_keys=True).encode("utf-8")
        self._client.put_object(
            Bucket=self.bucket,
            Key=self.key,
            Body=body,
            ContentType="application/json",
        )
        self._cache = data

    def is_processed(self, chunk_id: str) -> bool:
        rec = self._load().get(chunk_id)
        return rec is not None and rec.get("status") == "PROCESSED"

    def claim(self, chunk_id: str) -> bool:
        with self._lock:
            data = self._load()
            existing = data.get(chunk_id)
            if existing is not None:
                if existing.get("status") == "PROCESSED":
                    return False
                if time.time() - existing.get("claimed_at", 0) < self.PENDING_TTL_SEC:
                    return False
            data[chunk_id] = {"status": "PENDING", "claimed_at": time.time()}
            self._save(data)
        log.info("event=chunk_claimed chunk_id=%s backend=s3", chunk_id[:12])
        return True

    def mark_processed(self, chunk_id: str, embedding_uri: str) -> None:
        with self._lock:
            data = self._load()
            data[chunk_id] = {
                "status": "PROCESSED",
                "embedding_uri": embedding_uri,
                "processed_at": time.time(),
            }
            self._save(data)
        log.info(
            "event=chunk_processed chunk_id=%s uri=%s backend=s3",
            chunk_id[:12], embedding_uri,
        )

    def release(self, chunk_id: str) -> None:
        with self._lock:
            data = self._load()
            existing = data.get(chunk_id)
            if existing is not None and existing.get("status") == "PENDING":
                del data[chunk_id]
                self._save(data)
        log.info("event=chunk_released chunk_id=%s backend=s3", chunk_id[:12])

    def stats(self) -> dict[str, int]:
        data = self._load()
        return {
            "processed": sum(1 for r in data.values() if r.get("status") == "PROCESSED"),
            "pending": sum(1 for r in data.values() if r.get("status") == "PENDING"),
        }


# ===========================================================================
# Factory — pick a backend from env vars
# ===========================================================================


def make_store_from_env() -> IdempotencyStore:
    """Construct the backend named by IDEMPOTENCY_BACKEND.

    IDEMPOTENCY_BACKEND=dynamodb → DynamoDBStore (table from IDEMPOTENCY_TABLE)
    IDEMPOTENCY_BACKEND=s3       → S3ManifestStore (bucket from MANIFEST_BUCKET)
    IDEMPOTENCY_BACKEND=local    → LocalManifestStore (path from MANIFEST_PATH)
    """
    backend = os.environ.get("IDEMPOTENCY_BACKEND", "local")
    if backend == "dynamodb":
        table = os.environ["IDEMPOTENCY_TABLE"]
        return DynamoDBStore(table)
    if backend == "s3":
        bucket = os.environ["MANIFEST_BUCKET"]
        key = os.environ.get("MANIFEST_KEY", "embeddings/manifest.json")
        return S3ManifestStore(bucket, key)
    if backend == "local":
        path = Path(os.environ.get("MANIFEST_PATH", "./manifest.json"))
        return LocalManifestStore(path)
    raise ValueError(f"Unknown IDEMPOTENCY_BACKEND={backend!r}")
