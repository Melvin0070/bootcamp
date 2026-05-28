"""Dead-letter queue handler — observe (and optionally replay) poison messages.

Wired to the DLQ that collects messages exhausted by the SQS worker's retries
(redrive_policy in Terraform, PR11). It records each dead letter as a
structured error log (so Logs Insights / alarms can catch them) and returns the
batch for ops tooling. Re-drive (DLQ → source) is an operator action; see the
runbook.
"""

from __future__ import annotations

from typing import Any

from fossilrag.logging import configure_logging, get_logger

log = get_logger("worker.dlq")


def dlq_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Log dead-letter messages for observability; return them for tooling."""
    configure_logging()
    dead: list[dict[str, str]] = []
    for record in event.get("Records", []):
        message_id = record.get("messageId", "")
        body = record.get("body", "")
        log.error("event=dead_letter message_id=%s body=%s", message_id, body[:500])
        dead.append({"message_id": message_id, "body": body})
    log.info("event=dlq_drained count=%d", len(dead))
    return {"dead_letters": dead}
