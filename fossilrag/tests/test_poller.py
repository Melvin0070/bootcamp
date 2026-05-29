"""Local SQS poller: receive -> hand to sqs_handler -> delete the successes."""

from __future__ import annotations

import json

import pytest

from fossilrag.worker.poller import poll_once


def test_poll_once_empty_queue_returns_zeros():
    pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    from moto import mock_aws

    with mock_aws():
        sqs = boto3.client("sqs", region_name="us-east-1")
        url = sqs.create_queue(QueueName="q")["QueueUrl"]
        # A handler that should never be called when there are no messages.
        called = []

        def handler(event):  # noqa: ANN001
            called.append(event)
            return {"batchItemFailures": []}

        stats = poll_once(sqs, url, handler=handler, wait_seconds=0)
        assert stats == {"received": 0, "deleted": 0, "failed": 0}
        assert called == []


def test_poll_once_deletes_successes_and_keeps_failures():
    pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    from moto import mock_aws

    with mock_aws():
        sqs = boto3.client("sqs", region_name="us-east-1")
        url = sqs.create_queue(QueueName="q")["QueueUrl"]
        sqs.send_message(QueueUrl=url, MessageBody=json.dumps({"bucket": "raw", "key": "ok.txt"}))
        sqs.send_message(QueueUrl=url, MessageBody=json.dumps({"bucket": "raw", "key": "bad.txt"}))

        seen = {}

        def handler(event):  # noqa: ANN001 — fail whichever record carries bad.txt
            seen["event"] = event
            failures = []
            for rec in event["Records"]:
                if "bad.txt" in rec["body"]:
                    failures.append({"itemIdentifier": rec["messageId"]})
            return {"batchItemFailures": failures}

        stats = poll_once(sqs, url, handler=handler, wait_seconds=0)
        assert stats["received"] == 2
        assert stats["deleted"] == 1  # ok.txt deleted
        assert stats["failed"] == 1  # bad.txt left for redrive

        # The poller adapts SQS messages into the Lambda event shape the
        # handler expects (messageId + body + receiptHandle per record).
        rec0 = seen["event"]["Records"][0]
        assert {"messageId", "body", "receiptHandle"} <= set(rec0)

        # The failed message is still on the queue (invisible now); the success
        # is gone. After its visibility lapses, one message remains in flight.
        remaining = sqs.get_queue_attributes(
            QueueUrl=url, AttributeNames=["ApproximateNumberOfMessagesNotVisible"]
        )["Attributes"]
        assert int(remaining["ApproximateNumberOfMessagesNotVisible"]) == 1
