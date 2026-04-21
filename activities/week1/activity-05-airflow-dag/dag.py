"""
Fixed Airflow DAG — fossil ingestion pipeline.

Fixes applied (each annotated with ✅):
  1. retries=3 + retry_exponential_backoff=True — survives transient Lambda timeouts
  2. on_failure_callback fires Slack + email after all retries are exhausted
  3. execution_timeout per task — prevents silent hangs
  4. Structured JSON logging — machine-parseable, queryable in CloudWatch Insights
  5. All config via environment variables — no hardcoded values
  6. FunctionError detection — Lambda can return HTTP 200 with an error payload
"""

import json
import logging
import os
import smtplib
import socket
from datetime import datetime, timedelta
from email.mime.text import MIMEText

import boto3
import requests
from airflow import DAG
from airflow.operators.python import PythonOperator

# --------------------------------------------------------------------------- #
# ✅ FIX 4: Structured JSON logging                                             #
# --------------------------------------------------------------------------- #


class _JsonFormatter(logging.Formatter):
    """Emit each record as a single JSON line — parseable by CloudWatch Insights."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "level": record.levelname,
            "logger": record.name,
            "dag_id": getattr(record, "dag_id", "fossil_pipeline"),
            "task_id": getattr(record, "task_id", ""),
            "run_id": getattr(record, "run_id", ""),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _get_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        _h = logging.StreamHandler()
        _h.setFormatter(_JsonFormatter())
        log.addHandler(_h)
    log.setLevel(logging.INFO)
    return log


logger = _get_logger(__name__)

# --------------------------------------------------------------------------- #
# ✅ FIX 5: All config from environment — no hardcoded values                  #
# --------------------------------------------------------------------------- #

EXTRACT_LAMBDA_ARN = os.environ.get("EXTRACT_LAMBDA_ARN", "")
TRANSFORM_LAMBDA_ARN = os.environ.get("TRANSFORM_LAMBDA_ARN", "")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "25"))
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
# Lambda default max timeout is 900s; add 60s buffer for SDK overhead + cold starts
TASK_TIMEOUT_SECONDS = int(os.environ.get("LAMBDA_TIMEOUT_SECONDS", "900")) + 60

# --------------------------------------------------------------------------- #
# ✅ FIX 2: Alert callbacks — Slack + email, independent of each other         #
# --------------------------------------------------------------------------- #


def _build_alert_text(context: dict) -> str:
    ti = context.get("task_instance") or context.get("ti")
    return (
        ":rotating_light: *Airflow task failed*\n"
        f"• DAG: `{context['dag'].dag_id}`\n"
        f"• Task: `{ti.task_id if ti else 'unknown'}`\n"
        f"• Run: `{context.get('run_id', '')}`\n"
        f"• Execution date: `{context.get('execution_date', '')}`\n"
        f"• Exception: `{context.get('exception', '')}`\n"
        f"• Try number: `{ti.try_number if ti else '?'}`"
    )


def _send_slack_alert(context: dict) -> None:
    if not SLACK_WEBHOOK_URL:
        logger.warning("SLACK_WEBHOOK_URL not set — skipping Slack alert")
        return
    try:
        resp = requests.post(
            SLACK_WEBHOOK_URL,
            json={"text": _build_alert_text(context)},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Slack alert sent", extra={"task_id": "on_failure_callback"})
    except Exception:
        logger.exception("Slack alert failed", extra={"task_id": "on_failure_callback"})


def _send_email_alert(context: dict) -> None:
    if not ALERT_EMAIL:
        logger.warning("ALERT_EMAIL not set — skipping email alert")
        return
    try:
        body = (
            _build_alert_text(context)
            .replace("`", "")
            .replace("*", "")
            .replace(":rotating_light:", "[ALERT]")
        )
        msg = MIMEText(body)
        msg["Subject"] = f"[Airflow] Task failed — {context['dag'].dag_id}"
        msg["From"] = f"airflow@{socket.gethostname()}"
        msg["To"] = ALERT_EMAIL
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
            smtp.sendmail(msg["From"], [ALERT_EMAIL], msg.as_string())
        logger.info("Email alert sent to %s", ALERT_EMAIL, extra={"task_id": "on_failure_callback"})
    except Exception as exc:
        logger.error(
            "Email alert failed: %s",
            exc,
            extra={"task_id": "on_failure_callback"},
            exc_info=True,
        )


def on_failure_callback(context: dict) -> None:
    """Composite alert: both channels attempted independently so one failure doesn't block the other."""
    _send_slack_alert(context)
    _send_email_alert(context)


# --------------------------------------------------------------------------- #
# ✅ FIX 1 + 6: Lambda helper with FunctionError detection                     #
# --------------------------------------------------------------------------- #


def _invoke_lambda(function_arn: str, payload: dict, task_id: str, run_id: str) -> dict:
    """Invoke Lambda and surface both HTTP errors and function-level errors."""
    extra = {"dag_id": "fossil_pipeline", "task_id": task_id, "run_id": run_id}

    if not function_arn:
        raise ValueError(f"Lambda ARN not configured for task '{task_id}'")

    logger.info("Invoking Lambda %s", function_arn, extra=extra)
    client = boto3.client("lambda", region_name=AWS_REGION)
    response = client.invoke(
        FunctionName=function_arn,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode(),
    )

    http_status = response["StatusCode"]
    logger.info("Lambda HTTP status: %d", http_status, extra=extra)

    # ✅ FIX 6: Lambda can return HTTP 200 with a FunctionError header when the
    # function itself raised an exception — the broken DAG missed this entirely.
    if "FunctionError" in response:
        error_detail = json.loads(response["Payload"].read())
        error_msg = error_detail.get("errorMessage", "unknown Lambda function error")
        logger.error("Lambda function error: %s", error_msg, extra=extra)
        raise RuntimeError(f"Lambda function error in '{function_arn}': {error_msg}")

    if http_status != 200:
        logger.error("Lambda returned HTTP %d", http_status, extra=extra)
        raise ValueError(f"Lambda '{function_arn}' returned HTTP {http_status}")

    result = json.loads(response["Payload"].read())
    logger.info("Lambda succeeded", extra=extra)
    return result


# --------------------------------------------------------------------------- #
# Task callables                                                                #
# --------------------------------------------------------------------------- #


def extract_fossils(**context) -> dict:
    ti = context["ti"]
    return _invoke_lambda(EXTRACT_LAMBDA_ARN, {"action": "extract"}, ti.task_id, context["run_id"])


def transform_fossils(**context) -> None:
    ti = context["ti"]
    _invoke_lambda(TRANSFORM_LAMBDA_ARN, {"action": "transform"}, ti.task_id, context["run_id"])


def notify_complete(**context) -> None:
    ti = context["ti"]
    logger.info(
        "Pipeline completed successfully",
        extra={"dag_id": "fossil_pipeline", "task_id": ti.task_id, "run_id": context["run_id"]},
    )


# --------------------------------------------------------------------------- #
# ✅ FIX 1 + 2 + 3: DAG with retries, callbacks, and timeouts                  #
# --------------------------------------------------------------------------- #

default_args = {
    "owner": "fossilrag",
    "depends_on_past": False,
    # Retry 3× with exponential backoff: 30s → 60s → 120s (capped at 10 min)
    "retries": 3,
    "retry_delay": timedelta(seconds=30),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=10),
    # Alert fires only after ALL retries are exhausted
    "on_failure_callback": on_failure_callback,
    # Hard per-task ceiling — prevents silent hangs on Lambda connectivity issues
    "execution_timeout": timedelta(seconds=TASK_TIMEOUT_SECONDS),
    "email_on_failure": False,  # handled by on_failure_callback above
    "email_on_retry": False,
}

with DAG(
    dag_id="fossil_pipeline",
    description="FossilRAG ingestion — extract → transform → notify",
    start_date=datetime(2024, 1, 1),
    schedule_interval="@daily",
    catchup=False,
    default_args=default_args,
    tags=["fossilrag", "lambda", "etl"],
) as dag:

    extract = PythonOperator(
        task_id="extract_fossils",
        python_callable=extract_fossils,
    )

    transform = PythonOperator(
        task_id="transform_fossils",
        python_callable=transform_fossils,
    )

    notify = PythonOperator(
        task_id="notify_complete",
        python_callable=notify_complete,
    )

    extract >> transform >> notify
