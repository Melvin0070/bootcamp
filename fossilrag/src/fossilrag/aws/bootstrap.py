"""Provision the FossilRAG topology against LocalStack (or moto, in tests).

The $0 mirror of the Terraform stack (PR11): idempotently create the medallion
buckets, the ingest queue + its DLQ (``maxReceiveCount`` redrive), the
idempotency + prompt-cache DynamoDB tables (PK ``key``, TTL ``ttl`` — matching
:class:`fossilrag.idempotency.DynamoDBStore`), and the raw-bucket → SQS
notification (with the queue policy that lets S3 publish). Every step tolerates
"already exists", so re-running is safe — the same self-healing posture as the
rest of the pipeline.

All functions take explicit boto3-shaped clients, so the whole module is driven
by ``moto`` in CI without LocalStack or an account.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fossilrag.config import Settings, get_settings
from fossilrag.logging import get_logger

log = get_logger("aws.bootstrap")


@dataclass(frozen=True)
class ResourceNames:
    """The names of every resource the local stack provisions."""

    raw_bucket: str
    silver_bucket: str
    gold_bucket: str
    ingest_queue: str
    ingest_dlq: str
    idempotency_table: str
    prompt_cache_table: str

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> ResourceNames:
        s = settings or get_settings()
        return cls(
            raw_bucket=s.raw_bucket,
            silver_bucket=s.silver_bucket or "fossilrag-silver",
            gold_bucket=s.gold_bucket,
            ingest_queue=s.ingest_queue,
            ingest_dlq=s.ingest_dlq,
            idempotency_table=s.idempotency_table or "fossilrag-idempotency",
            prompt_cache_table=s.prompt_cache_table or "fossilrag-prompt-cache",
        )

    @property
    def buckets(self) -> tuple[str, str, str]:
        return (self.raw_bucket, self.silver_bucket, self.gold_bucket)

    @property
    def tables(self) -> tuple[str, str]:
        return (self.idempotency_table, self.prompt_cache_table)


def _is_already_exists(exc: Exception) -> bool:
    """True for the assorted 'resource already exists' errors across services."""
    name = type(exc).__name__
    return name in {
        "BucketAlreadyOwnedByYou",
        "BucketAlreadyExists",
        "ResourceInUseException",  # DynamoDB CreateTable on an existing table
        "QueueAlreadyExists",
    }


def ensure_bucket(s3: Any, name: str) -> bool:
    """Create an S3 bucket if absent. Returns True if newly created."""
    try:
        s3.create_bucket(Bucket=name)
        log.info("event=bucket_created name=%s", name)
        return True
    except Exception as exc:  # noqa: BLE001 — idempotent: tolerate 'already exists'
        if _is_already_exists(exc) or "BucketAlreadyOwnedByYou" in str(exc):
            log.info("event=bucket_exists name=%s", name)
            return False
        raise


def ensure_queue(sqs: Any, name: str, *, attributes: dict[str, str] | None = None) -> str:
    """Create (or look up) a queue and apply ``attributes``. Returns its URL.

    ``create_queue`` is idempotent for the same name, but ignores differing
    attributes on an existing queue — so attributes are (re)applied explicitly
    via ``set_queue_attributes``, keeping redrive/visibility correct on re-run.
    """
    url = sqs.create_queue(QueueName=name).get("QueueUrl")
    if attributes:
        sqs.set_queue_attributes(QueueUrl=url, Attributes=attributes)
    log.info("event=queue_ready name=%s", name)
    return url


def queue_arn(sqs: Any, queue_url: str) -> str:
    resp = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])
    return resp["Attributes"]["QueueArn"]


def ensure_table(dynamodb: Any, name: str) -> bool:
    """Create a PAY_PER_REQUEST table (PK ``key``) with TTL on ``ttl``.

    Matches the schema :class:`fossilrag.idempotency.DynamoDBStore` and the
    prompt-cache store expect. Returns True if newly created.
    """
    created = False
    try:
        dynamodb.create_table(
            TableName=name,
            AttributeDefinitions=[{"AttributeName": "key", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "key", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        created = True
        log.info("event=table_created name=%s", name)
    except Exception as exc:  # noqa: BLE001 — idempotent
        if not _is_already_exists(exc):
            raise
        log.info("event=table_exists name=%s", name)
    # Wait for ACTIVE (no-op on moto; matters on LocalStack) before TTL.
    try:
        dynamodb.get_waiter("table_exists").wait(TableName=name)
    except Exception:  # noqa: BLE001 — waiter unsupported on some fakes; best-effort
        log.debug("event=table_waiter_skipped name=%s", name)
    try:
        dynamodb.update_time_to_live(
            TableName=name,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
        )
    except Exception:  # noqa: BLE001 — already enabled / not supported → fine
        log.debug("event=ttl_already_set name=%s", name)
    return created


def _s3_publish_policy(queue_arn_: str, raw_bucket: str) -> str:
    """Queue policy letting the raw bucket publish ObjectCreated events to SQS."""
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AllowRawBucketPublish",
                    "Effect": "Allow",
                    "Principal": {"Service": "s3.amazonaws.com"},
                    "Action": "sqs:SendMessage",
                    "Resource": queue_arn_,
                    "Condition": {"ArnEquals": {"aws:SourceArn": f"arn:aws:s3:::{raw_bucket}"}},
                }
            ],
        }
    )


def ensure_raw_notification(s3: Any, sqs: Any, raw_bucket: str, ingest_url: str) -> str:
    """Wire raw-bucket ``s3:ObjectCreated:*`` → the ingest queue. Returns the ARN."""
    arn = queue_arn(sqs, ingest_url)
    sqs.set_queue_attributes(
        QueueUrl=ingest_url, Attributes={"Policy": _s3_publish_policy(arn, raw_bucket)}
    )
    s3.put_bucket_notification_configuration(
        Bucket=raw_bucket,
        NotificationConfiguration={
            "QueueConfigurations": [{"QueueArn": arn, "Events": ["s3:ObjectCreated:*"]}]
        },
    )
    log.info("event=raw_notification_wired bucket=%s queue_arn=%s", raw_bucket, arn)
    return arn


def provision(
    *,
    s3: Any,
    sqs: Any,
    dynamodb: Any,
    names: ResourceNames,
    max_receive_count: int = 5,
) -> dict[str, Any]:
    """Idempotently create the full local topology. Returns a summary."""
    for b in names.buckets:
        ensure_bucket(s3, b)

    dlq_url = ensure_queue(sqs, names.ingest_dlq, attributes={"MessageRetentionPeriod": "1209600"})
    dlq_arn = queue_arn(sqs, dlq_url)
    ingest_url = ensure_queue(
        sqs,
        names.ingest_queue,
        attributes={
            "VisibilityTimeout": "180",
            "RedrivePolicy": json.dumps(
                {"deadLetterTargetArn": dlq_arn, "maxReceiveCount": max_receive_count}
            ),
        },
    )

    for t in names.tables:
        ensure_table(dynamodb, t)

    notification_arn = ensure_raw_notification(s3, sqs, names.raw_bucket, ingest_url)

    summary = {
        "buckets": list(names.buckets),
        "queues": {"ingest": ingest_url, "dlq": dlq_url},
        "tables": list(names.tables),
        "notification_queue_arn": notification_arn,
    }
    log.info("event=provision_complete buckets=%d tables=%d", len(names.buckets), len(names.tables))
    return summary
