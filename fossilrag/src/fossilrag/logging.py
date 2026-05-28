"""Structured ``key=value`` logging, consistent with the bootcamp house style.

One log line per event, machine-parseable by CloudWatch Logs Insights and
human-readable in a terminal. The same ``event=...`` convention used by the
ingestion/observability activities is kept here so a single Logs Insights
filter selects records across the whole system.
"""

from __future__ import annotations

import logging
import os

_CONFIGURED = False


def configure_logging(level: str | None = None) -> None:
    """Idempotently configure root logging. Safe to call from every entrypoint."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=level or os.environ.get("FOSSILRAG_LOG_LEVEL", os.environ.get("LOG_LEVEL", "INFO")),
        format="%(asctime)s level=%(levelname)s logger=%(name)s %(message)s",
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger (``fossilrag.<name>``)."""
    return logging.getLogger(f"fossilrag.{name}" if not name.startswith("fossilrag") else name)
