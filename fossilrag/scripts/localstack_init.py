"""Provision the FossilRAG topology on LocalStack (the `aws` compose profile).

Runs once at stack start (the compose `bootstrap` service) and stands up — at
$0, against LocalStack — the same resources Terraform deploys to AWS: the
medallion buckets, the ingest queue + DLQ, the DynamoDB ledger/cache tables,
and the raw-bucket → SQS notification. Idempotent: safe to re-run.

    AWS_ENDPOINT_URL=http://localhost:4566 python -m scripts.localstack_init
"""

from __future__ import annotations

import json

from fossilrag.aws import ResourceNames, make_client, provision
from fossilrag.config import get_settings
from fossilrag.logging import configure_logging, get_logger

log = get_logger("scripts.localstack_init")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    names = ResourceNames.from_settings(settings)
    summary = provision(
        s3=make_client("s3", settings),
        sqs=make_client("sqs", settings),
        dynamodb=make_client("dynamodb", settings),
        names=names,
        max_receive_count=settings.sqs_max_receive_count,
    )
    log.info("event=bootstrap_done")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
