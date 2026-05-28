"""Resilient async workers: SQS-driven ingestion + DLQ handling.

The application side of the Auto-Scaling-Lambda-with-DLQ mutation: an SQS
worker with partial-batch-failure reporting, retry/backoff, and idempotent
reprocessing, plus a DLQ handler. The autoscaling/concurrency knobs themselves
are Terraform (PR11).
"""

from fossilrag.worker.dlq import dlq_handler
from fossilrag.worker.retry import retry_with_backoff
from fossilrag.worker.sqs import sqs_handler

__all__ = ["sqs_handler", "dlq_handler", "retry_with_backoff"]
