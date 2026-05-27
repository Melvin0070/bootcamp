"""
CloudWatch Embedded Metric Format (EMF) emitter.

Why EMF and not PutMetricData?
------------------------------
- Zero extra API calls. The Lambda already writes logs; EMF piggybacks
  on the existing log stream. PutMetricData would add a network call
  on every metric (and adds an IAM permission, and a synchronous
  point of failure in the hot path).
- Free with CloudWatch Logs at the same retention you already pay for.
- One JSON line is parseable by humans, by Logs Insights, AND by the
  CloudWatch metrics back-end. The same record drives the dashboard
  and shows up in `fields @message | filter event="pipeline_done"`.
- Tests are trivial: parse the captured stdout line and assert on the
  shape. No moto, no AWS, no network.

The format is documented at
https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Specification.html
— this implementation covers the subset the pipeline needs (one
namespace, fixed dimension set, multiple metrics per record).

We roll our own ~50-line emitter rather than depending on
`aws-embedded-metrics` so the Lambda deploy bundle stays small and the
test suite doesn't need a fake CloudWatch agent. The package is great;
the dependency surface isn't worth it for this use case.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import contextmanager
from typing import Any

log = logging.getLogger("fossilrag.pipeline")


NAMESPACE = "FossilRAG/Pipeline"


def emit(
    metrics: dict[str, tuple[float, str]],
    *,
    dimensions: dict[str, str] | None = None,
    properties: dict[str, Any] | None = None,
    namespace: str = NAMESPACE,
    stream=None,
) -> dict[str, Any]:
    """Emit one EMF record.

    Parameters
    ----------
    metrics
        ``{metric_name: (value, unit)}``. Unit must be one of the
        CloudWatch units (``Count``, ``Milliseconds``, ``Bytes``,
        ``Percent``, ``None``, etc.). The emitter does not validate
        — CloudWatch silently drops unknown units.
    dimensions
        Key/value pairs CloudWatch indexes the metric by. Keep the
        cardinality LOW. ``Stage`` ∈ {read, normalise, write, upload}
        is fine; ``RequestId`` is not — it would create one metric
        series per invocation and blow up the bill.
    properties
        Extra fields included in the log record but NOT as metrics.
        Great for high-cardinality context like ``request_id``.
    namespace
        CloudWatch metric namespace. Default is ``FossilRAG/Pipeline``.

    Returns
    -------
    The serialised record (also written to ``stream``). Returned for
    test convenience.
    """
    dimensions = dimensions or {}
    properties = properties or {}

    dim_keys = list(dimensions.keys())
    metric_defs = [{"Name": name, "Unit": unit} for name, (_, unit) in metrics.items()]

    record: dict[str, Any] = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    # Single dimension-set keeps things simple. If you
                    # need a totals row, emit two sets — but only if
                    # you actually use the second one in a dashboard.
                    "Dimensions": [dim_keys] if dim_keys else [[]],
                    "Metrics": metric_defs,
                }
            ],
        },
    }
    record.update(dimensions)
    record.update(properties)
    for name, (value, _unit) in metrics.items():
        record[name] = value

    line = json.dumps(record, separators=(",", ":"))
    # Resolve stdout at call time, not at function-definition time, so
    # pytest's capsys (which patches sys.stdout per test) sees the line.
    print(line, file=stream if stream is not None else sys.stdout)
    return record


@contextmanager
def stage_timer(stage: str, *, request_id: str | None = None):
    """Context manager that emits Latency + Success/Failure for a stage.

    Usage::

        with stage_timer("normalise", request_id=req):
            df = normalise(df)

    On exit the timer emits two metrics:
        - ``Latency`` (Milliseconds, dimension ``Stage=normalise``)
        - ``Errors`` (Count, dimension ``Stage=normalise``; 0 on
          success, 1 on exception)

    The exception is re-raised after the metric fires, so callers
    don't lose the failure — they just get a guaranteed metric on the
    way out. This is the pattern that fixes the "silent failure"
    anti-pattern from the broken baseline.
    """
    t0 = time.perf_counter()
    success = True
    try:
        yield
    except Exception:
        success = False
        raise
    finally:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        props: dict[str, Any] = {"event": f"stage_{stage}_done", "success": success}
        if request_id:
            props["request_id"] = request_id
        emit(
            {
                "Latency": (elapsed_ms, "Milliseconds"),
                "Errors": (0 if success else 1, "Count"),
            },
            dimensions={"Stage": stage},
            properties=props,
        )
        # Structured log line for Logs Insights. Same data, human-friendly.
        log.info(
            "event=stage_done stage=%s success=%s latency_ms=%.2f request_id=%s",
            stage,
            success,
            elapsed_ms,
            request_id or "-",
        )
