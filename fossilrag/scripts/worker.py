"""Local ingestion worker (the `aws` compose profile's `worker` service).

Stands in for the AWS Lambda event-source mapping: long-polls the ingest queue
and feeds each batch to the SAME :func:`fossilrag.worker.sqs.sqs_handler` the
cloud runs, deleting the successes and leaving failures for redrive → DLQ. One
handler, two drivers — the cloud's ESM and this loop.

    AWS_ENDPOINT_URL=http://localhost:4566 \
    FOSSILRAG_SILVER_BUCKET=fossilrag-silver \
    FOSSILRAG_IDEMPOTENCY_BACKEND=dynamodb \
    python -m scripts.worker
"""

from __future__ import annotations

from fossilrag.aws import make_client
from fossilrag.config import get_settings
from fossilrag.logging import configure_logging, get_logger
from fossilrag.worker.poller import run_forever
from fossilrag.worker.sqs import sqs_handler

log = get_logger("scripts.worker")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    sqs = make_client("sqs", settings)
    queue_url = sqs.get_queue_url(QueueName=settings.ingest_queue)["QueueUrl"]
    log.info("event=worker_ready queue=%s", settings.ingest_queue)
    run_forever(
        sqs,
        queue_url,
        handler=sqs_handler,
        max_messages=settings.sqs_max_messages,
        wait_seconds=settings.sqs_wait_seconds,
    )


if __name__ == "__main__":
    main()
