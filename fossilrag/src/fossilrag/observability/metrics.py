"""CloudWatch Embedded Metric Format (EMF) emitter.

Emitting one EMF JSON log line lets CloudWatch Logs extract a metric for free —
no ``PutMetricData`` calls, no extra IAM, and it works identically in a Lambda,
a container, or local dev (where it is just a structured log line). See the AWS
EMF spec: a line with an ``_aws.CloudWatchMetrics`` block names the namespace,
dimensions and metrics; the sibling top-level keys carry the values.

``build_emf`` is a pure function (deterministic given ``timestamp_ms``) so the
shape is unit-tested without touching the clock or stdout.
"""

from __future__ import annotations

import contextlib
import json
import time
from typing import Any

from fossilrag.logging import get_logger

log = get_logger("metrics")

DEFAULT_NAMESPACE = "FossilRAG"


def build_emf(
    metrics: dict[str, float],
    *,
    namespace: str = DEFAULT_NAMESPACE,
    units: dict[str, str] | None = None,
    dimensions: dict[str, str] | None = None,
    properties: dict[str, Any] | None = None,
    timestamp_ms: int,
) -> dict[str, Any]:
    """Build an EMF document for one or more metrics sharing a dimension set.

    ``metrics`` maps metric name → value; ``units`` optionally maps the same
    names → a CloudWatch unit (default ``Count``). ``dimensions`` become the
    metric dimensions AND top-level fields; ``properties`` are extra
    (non-dimension) context fields attached to the log record.
    """
    units = units or {}
    dimensions = dimensions or {}
    metric_defs = [{"Name": name, "Unit": units.get(name, "Count")} for name in metrics]
    doc: dict[str, Any] = {
        "_aws": {
            "Timestamp": timestamp_ms,
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    "Dimensions": [list(dimensions)] if dimensions else [[]],
                    "Metrics": metric_defs,
                }
            ],
        },
    }
    # Dimension values + metric values + free-form properties are sibling keys.
    doc.update(dimensions)
    doc.update(metrics)
    if properties:
        doc.update(properties)
    return doc


def emit_metric(
    name: str,
    value: float,
    *,
    unit: str = "Count",
    namespace: str = DEFAULT_NAMESPACE,
    dimensions: dict[str, str] | None = None,
    **properties: Any,
) -> dict[str, Any]:
    """Emit a single metric as an EMF log line. Returns the document (for tests)."""
    doc = build_emf(
        {name: value},
        namespace=namespace,
        units={name: unit},
        dimensions=dimensions,
        properties=properties or None,
        timestamp_ms=int(time.time() * 1000),
    )
    # One compact JSON line — CloudWatch extracts the metric from it. A metric
    # must never break the request path, so emission is fail-open.
    with contextlib.suppress(Exception):
        log.info(json.dumps(doc, separators=(",", ":")))
    return doc
