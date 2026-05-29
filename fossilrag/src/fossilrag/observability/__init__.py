"""Observability: CloudWatch EMF metrics + request-correlation/security middleware.

The serverless-native, $0-testable observability story:

* **EMF** (:mod:`fossilrag.observability.metrics`) — emit metrics as Embedded
  Metric Format log lines; CloudWatch Logs auto-extracts them into metrics with
  no ``PutMetricData`` API calls (works unchanged in Lambda).
* **Middleware** (:mod:`fossilrag.observability.middleware`) — a request-id /
  correlation-id filter so every log line and response carries ``X-Request-ID``,
  plus baseline security response headers.

X-Ray tracing is already enabled on the Lambdas (``tracing_config`` in
``infra/lambda.tf``); full OpenTelemetry/ADOT instrumentation is the documented
next step, deliberately not vendored here so the tested surface stays runnable
at $0.
"""

from fossilrag.observability.metrics import build_emf, emit_metric

__all__ = ["emit_metric", "build_emf"]
