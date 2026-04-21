"""
pytest suite for Activity 5 — Airflow DAG silent-failure fix.

Tests use AST parsing and source-code analysis so no running Airflow
instance is required. Two test targets:
  - broken/dag.py  → must have the known anti-patterns
  - dag.py         → must have all fixes
"""

import ast
import os
import re

import pytest

BASE = os.path.join(os.path.dirname(__file__), "..")
BROKEN_DAG_PATH = os.path.join(BASE, "broken", "dag.py")
FIXED_DAG_PATH = os.path.join(BASE, "dag.py")
ENV_EXAMPLE_PATH = os.path.join(BASE, ".env.example")


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #


def _src(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _tree(path: str) -> ast.Module:
    return ast.parse(_src(path))


def _dict_key_values(tree: ast.Module) -> list[tuple]:
    """Return all (key_constant, value_node) pairs from every Dict literal."""
    pairs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant):
                    pairs.append((k.value, v))
    return pairs


def _int_value(node: ast.expr) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    return None


# --------------------------------------------------------------------------- #
# 1. Broken DAG anti-patterns                                                   #
# --------------------------------------------------------------------------- #


class TestBrokenDagAntiPatterns:
    """Verify the broken DAG has exactly the documented bugs."""

    def test_no_retries_key(self):
        src = _src(BROKEN_DAG_PATH)
        # default_args either absent or has no 'retries' key
        assert "retries" not in src, "broken DAG must not configure retries"

    def test_no_on_failure_callback(self):
        src = _src(BROKEN_DAG_PATH)
        assert "on_failure_callback" not in src, "broken DAG must not have on_failure_callback"

    def test_no_execution_timeout(self):
        src = _src(BROKEN_DAG_PATH)
        assert "execution_timeout" not in src, "broken DAG must not set execution_timeout"

    def test_uses_print_not_logging(self):
        src = _src(BROKEN_DAG_PATH)
        assert "print(" in src, "broken DAG should use print() instead of logging"
        assert "import logging" not in src, "broken DAG must not import logging"

    def test_hardcoded_lambda_function_names(self):
        src = _src(BROKEN_DAG_PATH)
        # Both function names are string literals in the source
        assert "fossil-extractor" in src, "broken DAG must hardcode extractor function name"
        assert "fossil-transformer" in src, "broken DAG must hardcode transformer function name"

    def test_no_retry_exponential_backoff(self):
        src = _src(BROKEN_DAG_PATH)
        assert "retry_exponential_backoff" not in src, "broken DAG must not configure exponential backoff"

    def test_function_error_not_checked(self):
        src = _src(BROKEN_DAG_PATH)
        assert "FunctionError" not in src, "broken DAG must not check for Lambda FunctionError"


# --------------------------------------------------------------------------- #
# 2. Fixed DAG — retries with exponential backoff                               #
# --------------------------------------------------------------------------- #


class TestFixedDagRetries:

    def test_retries_key_present(self):
        src = _src(FIXED_DAG_PATH)
        assert "retries" in src

    def test_retry_exponential_backoff_enabled(self):
        src = _src(FIXED_DAG_PATH)
        assert "retry_exponential_backoff" in src
        # The value must be True
        assert re.search(r"retry_exponential_backoff\s*[=:]\s*True", src)

    def test_retry_delay_uses_timedelta(self):
        src = _src(FIXED_DAG_PATH)
        assert "retry_delay" in src
        assert "timedelta" in src

    def test_max_retry_delay_configured(self):
        src = _src(FIXED_DAG_PATH)
        assert "max_retry_delay" in src

    def test_retries_count_at_least_3(self):
        pairs = _dict_key_values(_tree(FIXED_DAG_PATH))
        retries_vals = [_int_value(v) for k, v in pairs if k == "retries"]
        valid = [n for n in retries_vals if n is not None and n >= 3]
        assert valid, f"retries must be >= 3, found: {retries_vals}"

    def test_retry_delay_at_least_10_seconds(self):
        """Base delay must be non-trivial — guards against retry_delay=timedelta(0)."""
        src = _src(FIXED_DAG_PATH)
        # Find timedelta(seconds=N) patterns near retry_delay
        matches = re.findall(r"timedelta\(seconds=(\d+)\)", src)
        values = [int(m) for m in matches]
        assert any(v >= 10 for v in values), f"retry_delay timedelta seconds must be >= 10, found: {values}"


# --------------------------------------------------------------------------- #
# 3. Fixed DAG — alerts                                                         #
# --------------------------------------------------------------------------- #


class TestFixedDagAlerts:

    def test_on_failure_callback_in_default_args(self):
        src = _src(FIXED_DAG_PATH)
        assert "on_failure_callback" in src

    def test_slack_function_defined(self):
        src = _src(FIXED_DAG_PATH)
        assert "slack" in src.lower(), "fixed DAG must contain a Slack alert function"

    def test_email_function_defined(self):
        src = _src(FIXED_DAG_PATH)
        assert "email" in src.lower(), "fixed DAG must contain an email alert function"

    def test_slack_uses_env_var(self):
        src = _src(FIXED_DAG_PATH)
        assert "SLACK_WEBHOOK_URL" in src
        assert "os.environ" in src

    def test_email_uses_env_var(self):
        src = _src(FIXED_DAG_PATH)
        assert "ALERT_EMAIL" in src

    def test_composite_callback_calls_slack_and_email(self):
        """The on_failure_callback must invoke both alert helpers."""
        src = _src(FIXED_DAG_PATH)
        assert "_send_slack_alert" in src
        assert "_send_email_alert" in src
        # Both calls should appear inside on_failure_callback body
        cb_match = re.search(
            r"def on_failure_callback.*?(?=\ndef |\Z)", src, re.DOTALL
        )
        assert cb_match, "on_failure_callback function must be defined"
        body = cb_match.group(0)
        assert "_send_slack_alert" in body
        assert "_send_email_alert" in body

    def test_alert_gracefully_handles_missing_env_var(self):
        """If webhook URL is empty, code must skip rather than crash."""
        src = _src(FIXED_DAG_PATH)
        # Webhook is read with .get() — empty string is a valid "not configured" sentinel
        assert "os.environ.get" in src or ".get(" in src


# --------------------------------------------------------------------------- #
# 4. Fixed DAG — structured logging                                             #
# --------------------------------------------------------------------------- #


class TestFixedDagLogging:

    def test_uses_logging_not_print(self):
        src = _src(FIXED_DAG_PATH)
        assert "import logging" in src
        assert "print(" not in src, "fixed DAG must not use print()"

    def test_json_formatter_defined(self):
        src = _src(FIXED_DAG_PATH)
        assert "Formatter" in src, "fixed DAG must define a JSON log Formatter"
        assert "json.dumps" in src

    def test_logs_include_task_id(self):
        src = _src(FIXED_DAG_PATH)
        assert "task_id" in src

    def test_logs_include_run_id(self):
        src = _src(FIXED_DAG_PATH)
        assert "run_id" in src

    def test_logs_include_dag_id(self):
        src = _src(FIXED_DAG_PATH)
        assert "dag_id" in src

    def test_log_format_emits_timestamp(self):
        src = _src(FIXED_DAG_PATH)
        assert "timestamp" in src


# --------------------------------------------------------------------------- #
# 5. Fixed DAG — no hardcoded values (env vars only)                            #
# --------------------------------------------------------------------------- #


class TestFixedDagEnvVars:

    def test_no_hardcoded_extractor_name(self):
        src = _src(FIXED_DAG_PATH)
        assert "fossil-extractor" not in src, "extractor function name must not be hardcoded"

    def test_no_hardcoded_transformer_name(self):
        src = _src(FIXED_DAG_PATH)
        assert "fossil-transformer" not in src, "transformer function name must not be hardcoded"

    def test_extract_arn_from_env(self):
        src = _src(FIXED_DAG_PATH)
        assert "EXTRACT_LAMBDA_ARN" in src

    def test_transform_arn_from_env(self):
        src = _src(FIXED_DAG_PATH)
        assert "TRANSFORM_LAMBDA_ARN" in src

    def test_aws_region_from_env(self):
        src = _src(FIXED_DAG_PATH)
        assert "AWS_REGION" in src

    def test_env_example_exists(self):
        assert os.path.exists(ENV_EXAMPLE_PATH), ".env.example must exist"

    def test_env_example_has_lambda_arns(self):
        with open(ENV_EXAMPLE_PATH) as fh:
            content = fh.read()
        assert "EXTRACT_LAMBDA_ARN" in content
        assert "TRANSFORM_LAMBDA_ARN" in content

    def test_env_example_has_slack_webhook(self):
        with open(ENV_EXAMPLE_PATH) as fh:
            content = fh.read()
        assert "SLACK_WEBHOOK_URL" in content

    def test_env_example_has_alert_email(self):
        with open(ENV_EXAMPLE_PATH) as fh:
            content = fh.read()
        assert "ALERT_EMAIL" in content


# --------------------------------------------------------------------------- #
# 6. Fixed DAG — timeouts                                                       #
# --------------------------------------------------------------------------- #


class TestFixedDagTimeouts:

    def test_execution_timeout_in_default_args(self):
        src = _src(FIXED_DAG_PATH)
        assert "execution_timeout" in src

    def test_timeout_uses_timedelta(self):
        src = _src(FIXED_DAG_PATH)
        # execution_timeout must be a timedelta, not a raw integer
        assert re.search(r"execution_timeout.*timedelta", src, re.DOTALL)

    def test_lambda_function_error_detected(self):
        """HTTP 200 + FunctionError = Lambda crashed — must be caught."""
        src = _src(FIXED_DAG_PATH)
        assert "FunctionError" in src

    def test_lambda_arn_validated_before_invoke(self):
        """Empty ARN must raise before the boto3 call is made."""
        src = _src(FIXED_DAG_PATH)
        assert "not function_arn" in src or "if not function_arn" in src

    def test_timeout_env_var_configurable(self):
        src = _src(FIXED_DAG_PATH)
        assert "LAMBDA_TIMEOUT_SECONDS" in src

    def test_timeout_adds_buffer_over_lambda_max(self):
        """Airflow timeout should exceed the Lambda timeout to account for SDK overhead."""
        src = _src(FIXED_DAG_PATH)
        # Buffer comment or + 60 addition must be present
        assert "+ 60" in src or "+60" in src or "buffer" in src.lower()
