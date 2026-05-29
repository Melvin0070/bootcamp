"""Event-body parsing shared by the S3 handler and the SQS worker."""

from __future__ import annotations

import json

import pytest

from fossilrag.events import pair_from_s3_record, parse_task_bodies, s3_event_pairs


def _s3_event(*pairs: tuple[str, str]) -> str:
    """Build an S3 ObjectCreated notification body for the given (bucket, key)s."""
    return json.dumps(
        {"Records": [{"s3": {"bucket": {"name": b}, "object": {"key": k}}} for b, k in pairs]}
    )


def test_parse_direct_producer_task():
    assert parse_task_bodies(json.dumps({"bucket": "raw", "key": "a.txt"})) == [("raw", "a.txt")]


def test_parse_s3_event_single_record():
    assert parse_task_bodies(_s3_event(("raw", "docs/a.txt"))) == [("raw", "docs/a.txt")]


def test_parse_s3_event_multiple_records():
    body = _s3_event(("raw", "a.txt"), ("raw", "b.txt"))
    assert parse_task_bodies(body) == [("raw", "a.txt"), ("raw", "b.txt")]


def test_parse_s3_event_url_decodes_key():
    # S3 encodes spaces as '+' and other chars percent-encoded in event keys.
    body = _s3_event(("raw", "my+report%20final.txt"))
    assert parse_task_bodies(body) == [("raw", "my report final.txt")]


def test_parse_s3_test_event_yields_no_tasks():
    # S3 fires this once when a notification is configured; must not error.
    body = json.dumps(
        {"Service": "Amazon S3", "Event": "s3:TestEvent", "Bucket": "raw", "Time": "..."}
    )
    assert parse_task_bodies(body) == []


def test_parse_skips_malformed_s3_records():
    body = json.dumps({"Records": [{"s3": {"bucket": {}, "object": {}}}]})
    assert parse_task_bodies(body) == []


def test_parse_unrecognised_body_raises():
    with pytest.raises(ValueError, match="unrecognised"):
        parse_task_bodies(json.dumps({"foo": "bar"}))


def test_parse_non_object_body_raises():
    with pytest.raises(ValueError, match="must be a JSON object"):
        parse_task_bodies(json.dumps(["not", "an", "object"]))


def test_parse_invalid_json_raises():
    with pytest.raises(json.JSONDecodeError):
        parse_task_bodies("{not json")


def test_pair_from_s3_record_helpers():
    rec = {"s3": {"bucket": {"name": "b"}, "object": {"key": "k"}}}
    assert pair_from_s3_record(rec) == ("b", "k")
    assert pair_from_s3_record({"s3": {}}) is None
    assert s3_event_pairs([rec, {"s3": {}}]) == [("b", "k")]
