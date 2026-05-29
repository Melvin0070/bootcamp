"""Provisioning the LocalStack topology — driven by moto (no LocalStack/account)."""

from __future__ import annotations

import json

import pytest

from fossilrag.aws.bootstrap import ResourceNames, provision

NAMES = ResourceNames(
    raw_bucket="fr-raw",
    silver_bucket="fr-silver",
    gold_bucket="fr-gold",
    ingest_queue="fr-ingest",
    ingest_dlq="fr-ingest-dlq",
    idempotency_table="fr-idem",
    prompt_cache_table="fr-cache",
)


def _clients(boto3):  # noqa: ANN001
    return (
        boto3.client("s3", region_name="us-east-1"),
        boto3.client("sqs", region_name="us-east-1"),
        boto3.client("dynamodb", region_name="us-east-1"),
    )


def test_provision_creates_full_topology():
    pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    from moto import mock_aws

    with mock_aws():
        s3, sqs, dynamodb = _clients(boto3)
        summary = provision(s3=s3, sqs=sqs, dynamodb=dynamodb, names=NAMES, max_receive_count=5)

        # Buckets
        existing = {b["Name"] for b in s3.list_buckets()["Buckets"]}
        assert {"fr-raw", "fr-silver", "fr-gold"} <= existing

        # Queues + redrive policy (ingest -> DLQ after 5)
        attrs = sqs.get_queue_attributes(
            QueueUrl=summary["queues"]["ingest"],
            AttributeNames=["RedrivePolicy", "VisibilityTimeout"],
        )["Attributes"]
        redrive = json.loads(attrs["RedrivePolicy"])
        assert redrive["maxReceiveCount"] == 5
        assert NAMES.ingest_dlq in redrive["deadLetterTargetArn"]
        assert attrs["VisibilityTimeout"] == "180"

        # Tables exist with TTL on `ttl`
        for table in ("fr-idem", "fr-cache"):
            desc = dynamodb.describe_table(TableName=table)["Table"]
            assert desc["KeySchema"][0]["AttributeName"] == "key"
            ttl = dynamodb.describe_time_to_live(TableName=table)["TimeToLiveDescription"]
            assert ttl["TimeToLiveStatus"] in {"ENABLED", "ENABLING"}
            assert ttl.get("AttributeName") == "ttl"

        # raw -> SQS notification wired to the ingest queue
        notif = s3.get_bucket_notification_configuration(Bucket="fr-raw")
        queue_cfgs = notif.get("QueueConfigurations", [])
        assert any("fr-ingest" in q["QueueArn"] for q in queue_cfgs)
        assert any("s3:ObjectCreated:*" in q["Events"] for q in queue_cfgs)


def test_provision_is_idempotent():
    pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    from moto import mock_aws

    with mock_aws():
        s3, sqs, dynamodb = _clients(boto3)
        first = provision(s3=s3, sqs=sqs, dynamodb=dynamodb, names=NAMES)
        # Re-running must not raise and must yield the same queue URLs.
        second = provision(s3=s3, sqs=sqs, dynamodb=dynamodb, names=NAMES)
        assert first["queues"]["ingest"] == second["queues"]["ingest"]
        assert len({b["Name"] for b in s3.list_buckets()["Buckets"]}) == 3


def test_provisioned_idempotency_table_drives_the_ledger():
    """The bootstrap'd table satisfies DynamoDBStore's schema end-to-end."""
    pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    from moto import mock_aws

    from fossilrag.idempotency import DynamoDBStore

    with mock_aws():
        s3, sqs, dynamodb = _clients(boto3)
        provision(s3=s3, sqs=sqs, dynamodb=dynamodb, names=NAMES)
        store = DynamoDBStore("fr-idem", client=dynamodb)
        assert store.claim("sqs:fr-raw:a.txt") is True
        assert store.is_processed("sqs:fr-raw:a.txt") is False
        store.mark_processed("sqs:fr-raw:a.txt", "silver:a.txt")
        assert store.is_processed("sqs:fr-raw:a.txt") is True


def test_resource_names_from_settings_defaults():
    from fossilrag.config import Settings

    names = ResourceNames.from_settings(Settings(silver_bucket=None))
    assert names.raw_bucket == "fossilrag-raw"
    assert names.silver_bucket == "fossilrag-silver"  # default when unset
    assert names.idempotency_table == "fossilrag-idempotency"
    assert names.buckets == ("fossilrag-raw", "fossilrag-silver", "fossilrag-gold")
