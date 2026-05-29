"""Parse incoming event bodies into ``(bucket, key)`` ingestion tasks.

Shared by the two ingestion entrypoints — the direct-S3
:func:`fossilrag.ingest.handler.handler` and the SQS
:func:`fossilrag.worker.sqs.sqs_handler` — so there is exactly one S3-event
parser in the codebase. It lives at the package root (which imports nothing) on
purpose: both the ``ingest`` and ``worker`` packages import it, and a top-level
leaf module keeps that shared dependency cycle-free.

The ingest queue receives messages from two producers, and the worker must
accept both shapes:

  * **S3 event notifications** — what the raw bucket's ``s3:ObjectCreated:*``
    notification actually delivers (Terraform ``aws_s3_bucket_notification``,
    PR11). The body is ``{"Records": [{"s3": {"bucket": {"name": ...},
    "object": {"key": ...}}}, ...]}`` and one event may carry several records.
  * **Direct producer tasks** — ``{"bucket": ..., "key": ...}`` posted by an
    app/back-fill job that already knows the object location.

S3 also sends a one-off ``s3:TestEvent`` the moment a notification is wired up;
it carries no object, so it must parse to *no* tasks (skipped), never an error.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

# A logical ingestion task: which object, in which bucket, to pull into silver.
Task = tuple[str, str]


def pair_from_s3_record(record: dict[str, Any]) -> Task | None:
    """Extract ``(bucket, key)`` from one S3 event record, or ``None`` if malformed.

    S3 URL-encodes object keys in event notifications (spaces → ``+``), so the
    key is unquoted here — the single place that decoding lives.
    """
    s3 = record.get("s3", {})
    bucket = s3.get("bucket", {}).get("name")
    key = urllib.parse.unquote_plus(s3.get("object", {}).get("key", ""))
    if not bucket or not key:
        return None
    return bucket, key


def s3_event_pairs(records: list[dict[str, Any]]) -> list[Task]:
    """Map S3 event records to ``(bucket, key)`` tasks, dropping malformed ones."""
    return [pair for rec in records if (pair := pair_from_s3_record(rec)) is not None]


def parse_task_bodies(body: str) -> list[Task]:
    """Parse one SQS message body into zero or more ``(bucket, key)`` tasks.

    Accepts the S3-event-notification shape, the direct ``{"bucket","key"}``
    shape, and the contentless ``s3:TestEvent`` (→ no tasks). Raises
    ``ValueError`` on anything else so the worker can DLQ a genuinely
    unparseable message rather than silently dropping it.
    """
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError(f"SQS task body must be a JSON object, got {type(data).__name__}")
    # S3's connectivity probe, emitted once when the notification is configured.
    if data.get("Event") == "s3:TestEvent":
        return []
    if "Records" in data:
        return s3_event_pairs(data["Records"])
    if "bucket" in data and "key" in data:
        return [(data["bucket"], data["key"])]
    raise ValueError(f"unrecognised SQS task body (keys={sorted(data)})")
