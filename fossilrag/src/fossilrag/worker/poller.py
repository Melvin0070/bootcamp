"""Local SQS poller — the dev/compose driver for the ingestion worker.

In AWS, the Lambda event-source mapping pumps SQS batches into
:func:`fossilrag.worker.sqs.sqs_handler` and deletes the successes for you. The
free LocalStack tier has no Lambda-on-SQS pump, so locally this long-poll loop
plays that role: receive a batch, hand it to the *same* ``sqs_handler``
unchanged, then delete exactly the messages the handler did NOT report as
failures. Whatever it leaves becomes visible again and is redriven to the DLQ
after ``maxReceiveCount`` — identical semantics to the cloud, one handler, two
drivers.

:func:`poll_once` is the testable unit (driven by moto); :func:`run_forever` is
the thin daemon loop the worker container runs.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fossilrag.logging import get_logger

log = get_logger("worker.poller")

# A Lambda-style SQS handler: takes {"Records": [...]} → {"batchItemFailures": [...]}.
Handler = Callable[[dict[str, Any]], dict[str, Any]]


def poll_once(
    sqs: Any,
    queue_url: str,
    *,
    handler: Handler,
    max_messages: int = 10,
    wait_seconds: int = 10,
) -> dict[str, int]:
    """Receive one batch, run ``handler``, delete the non-failed messages.

    Returns ``{received, deleted, failed}``. Messages the handler reports in
    ``batchItemFailures`` are left on the queue (their visibility timeout
    lapses → SQS redelivers → DLQ after maxReceiveCount), mirroring the AWS
    partial-batch-failure contract exactly.
    """
    resp = sqs.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=max_messages,
        WaitTimeSeconds=wait_seconds,
    )
    messages = resp.get("Messages", [])
    if not messages:
        return {"received": 0, "deleted": 0, "failed": 0}

    records = [
        {
            "messageId": m["MessageId"],
            "body": m.get("Body", ""),
            "receiptHandle": m["ReceiptHandle"],
        }
        for m in messages
    ]
    result = handler({"Records": records})
    failed_ids = {f["itemIdentifier"] for f in result.get("batchItemFailures", [])}

    deleted = 0
    for m in messages:
        if m["MessageId"] in failed_ids:
            continue  # leave for redelivery / DLQ
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
        deleted += 1

    log.info(
        "event=poll_batch received=%d deleted=%d failed=%d",
        len(messages),
        deleted,
        len(failed_ids),
    )
    return {"received": len(messages), "deleted": deleted, "failed": len(failed_ids)}


def run_forever(  # pragma: no cover - daemon loop, exercised in the worker container
    sqs: Any,
    queue_url: str,
    *,
    handler: Handler,
    max_messages: int = 10,
    wait_seconds: int = 10,
    error_backoff: float = 2.0,
) -> None:
    """Poll ``queue_url`` until interrupted, handling errors without dying."""
    log.info("event=poller_start queue_url=%s", queue_url)
    while True:
        try:
            poll_once(
                sqs,
                queue_url,
                handler=handler,
                max_messages=max_messages,
                wait_seconds=wait_seconds,
            )
        except KeyboardInterrupt:
            log.info("event=poller_stop")
            return
        except Exception:
            log.exception("event=poll_error")
            time.sleep(error_backoff)
