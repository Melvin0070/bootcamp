"""Resilient worker: retry/backoff, SQS partial-batch + idempotency, DLQ."""

from __future__ import annotations

import contextlib
import json
import os

import pytest

from fossilrag.worker.dlq import dlq_handler
from fossilrag.worker.retry import retry_with_backoff


@contextlib.contextmanager
def _env(**kw):  # noqa: ANN001, ANN201
    from fossilrag.config import get_settings

    prev = {k: os.environ.get(k) for k in kw}
    for k, v in kw.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()
    try:
        yield
    finally:
        for k, old in prev.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old
        get_settings.cache_clear()


# --- retry/backoff (pure) -------------------------------------------------


def test_retry_succeeds_first_try():
    calls = []
    assert retry_with_backoff(lambda: calls.append(1) or "ok", sleeper=lambda d: None) == "ok"
    assert len(calls) == 1


def test_retry_succeeds_after_failures_with_backoff_schedule():
    delays: list[float] = []
    state = {"n": 0}

    def fn():
        state["n"] += 1
        if state["n"] < 3:
            raise ValueError("transient")
        return "ok"

    assert retry_with_backoff(fn, max_attempts=5, base_delay=1.0, sleeper=delays.append) == "ok"
    assert state["n"] == 3
    assert delays == [1.0, 2.0]  # base*2^0, base*2^1


def test_retry_exhausts_and_reraises():
    with pytest.raises(ValueError):
        retry_with_backoff(
            lambda: (_ for _ in ()).throw(ValueError("always")),
            max_attempts=3,
            sleeper=lambda d: None,
        )


def test_retry_only_retries_listed_exceptions():
    calls = []

    def fn():
        calls.append(1)
        raise KeyError("permanent")

    with pytest.raises(KeyError):
        retry_with_backoff(fn, retry_on=(ValueError,), sleeper=lambda d: None)
    assert len(calls) == 1  # KeyError not in retry_on → not retried


# --- DLQ handler ----------------------------------------------------------


def test_dlq_handler_records_dead_letters():
    event = {"Records": [{"messageId": "d1", "body": "poison1"}, {"messageId": "d2", "body": "p2"}]}
    out = dlq_handler(event)
    assert [d["message_id"] for d in out["dead_letters"]] == ["d1", "d2"]


# --- SQS worker (moto) ----------------------------------------------------


def _put(s3, key, body=b"Doc."):  # noqa: ANN001
    s3.put_object(Bucket="raw", Key=key, Body=body, ContentType="text/plain")


def test_sqs_handler_partial_batch_failure_and_idempotency(tmp_path):
    pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    from moto import mock_aws

    from fossilrag.worker.sqs import sqs_handler

    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="raw")
        s3.create_bucket(Bucket="silver")
        _put(s3, "a.txt", b"Doc A.")
        _put(s3, "b.txt", b"Doc B.")  # "missing.txt" intentionally absent → fails

        event = {
            "Records": [
                {"messageId": "m1", "body": json.dumps({"bucket": "raw", "key": "a.txt"})},
                {"messageId": "m2", "body": json.dumps({"bucket": "raw", "key": "b.txt"})},
                {"messageId": "m3", "body": json.dumps({"bucket": "raw", "key": "missing.txt"})},
            ]
        }
        with _env(
            FOSSILRAG_SILVER_BUCKET="silver",
            FOSSILRAG_IDEMPOTENCY_BACKEND="local",
            FOSSILRAG_IDEMPOTENCY_MANIFEST_PATH=str(tmp_path / "idem.json"),
            FOSSILRAG_SQS_BASE_DELAY="0",  # no real sleeps in the test
            AWS_ENDPOINT_URL=None,
        ):
            r = sqs_handler(event)
            # Only the missing object is returned for retry (partial batch).
            assert r["batchItemFailures"] == [{"itemIdentifier": "m3"}]
            keys = {o["Key"] for o in s3.list_objects_v2(Bucket="silver").get("Contents", [])}
            assert len(keys) == 2  # a + b written to silver

            # Re-deliver: a/b are already processed → skipped (idempotent); m3
            # still fails. No new silver writes.
            r2 = sqs_handler(event)
            assert r2["batchItemFailures"] == [{"itemIdentifier": "m3"}]
            keys2 = {o["Key"] for o in s3.list_objects_v2(Bucket="silver").get("Contents", [])}
            assert keys2 == keys
