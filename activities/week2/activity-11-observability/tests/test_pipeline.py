"""
Behaviour tests for pipeline.py — the observability-fixed version.

Covers the pure normalise() transform (parity with Activity 10), the
S3-integrated handler under a moto-stubbed S3, and the EMF metric
contract (the dashboard depends on these metric names existing).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import boto3
import pandas as pd
import pytest
from moto import mock_aws

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pipeline  # noqa: E402

# ---------------------------------------------------------------------------
# Pure transform — parity with Activity 10
# ---------------------------------------------------------------------------


class TestNormalise:
    def test_renames_columns(self):
        df = pd.DataFrame({"Species": ["Trex"], "Age_MYA": [66.0]})
        out, dropped = pipeline.normalise(df)
        assert list(out.columns) == ["species", "age_mya"]
        assert dropped == 0

    def test_drops_rows_missing_required_columns(self):
        df = pd.DataFrame({"Species": ["trex", None, "raptor"], "Age_MYA": [66.0, 70.0, None]})
        out, dropped = pipeline.normalise(df)
        assert len(out) == 1
        assert dropped == 2

    def test_drops_empty_string_species(self):
        df = pd.DataFrame({"Species": ["", "trex"], "Age_MYA": [66.0, 66.0]})
        out, dropped = pipeline.normalise(df)
        assert len(out) == 1
        assert dropped == 1

    def test_raises_when_required_column_missing(self):
        df = pd.DataFrame({"Species": ["trex"]})
        with pytest.raises(ValueError, match="missing required columns"):
            pipeline.normalise(df)

    def test_lowercases_and_strips_species(self):
        df = pd.DataFrame({"Species": ["  T-Rex  "], "Age_MYA": [66.0]})
        out, _ = pipeline.normalise(df)
        assert out.loc[0, "species"] == "t-rex"

    def test_does_not_mutate_input(self):
        df = pd.DataFrame({"Species": ["  Trex  "], "Age_MYA": [66.0]})
        before = df.copy()
        pipeline.normalise(df)
        pd.testing.assert_frame_equal(df, before)


# ---------------------------------------------------------------------------
# Handler end-to-end against moto S3
# ---------------------------------------------------------------------------


@pytest.fixture
def s3_with_csv():
    """Spin up a moto S3, drop a CSV in, reset module-level boto client."""
    with mock_aws():
        # Force pipeline.py to re-init its module-level S3 client inside
        # the moto context (otherwise it would be bound to a fake or
        # real client created earlier).
        pipeline._S3 = None
        client = boto3.client("s3", region_name="us-east-1")
        bucket = "fossilrag-test"
        client.create_bucket(Bucket=bucket)
        csv = b"Species,Age_MYA\nTrex,66.0\nRaptor,70.0\n,80.0\n"
        client.put_object(Bucket=bucket, Key="raw/in.csv", Body=csv)
        yield client, bucket


def _event(bucket: str, key: str) -> dict:
    return {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}


class TestHandler:
    def test_happy_path_writes_parquet_to_s3(self, s3_with_csv, capsys):
        client, bucket = s3_with_csv
        out = pipeline.handler(_event(bucket, "raw/in.csv"), None)
        assert out["statusCode"] == 200
        assert out["rows"] == 2  # the empty-species row is dropped
        # Verify the parquet was uploaded.
        keys = [o["Key"] for o in client.list_objects_v2(Bucket=bucket)["Contents"]]
        assert "raw/in.parquet" in keys

    def test_failure_propagates_and_emits_failure_metric(self, s3_with_csv, capsys):
        client, bucket = s3_with_csv
        client.put_object(Bucket=bucket, Key="raw/bad.csv", Body=b"NotSpecies,X\n1,2\n")
        with pytest.raises(ValueError, match="missing required columns"):
            pipeline.handler(_event(bucket, "raw/bad.csv"), None)
        out_lines = [
            ln for ln in capsys.readouterr().out.splitlines() if ln.strip().startswith("{")
        ]
        records = [json.loads(ln) for ln in out_lines]
        events = [r.get("event") for r in records]
        assert "pipeline_failed" in events
        # Stage records all share event="stage_done"; the per-stage value
        # lives in the `Stage` field. Find the normalise stage record and
        # assert it reports Errors=1.
        norm = next(
            r for r in records if r.get("event") == "stage_done" and r.get("Stage") == "normalise"
        )
        assert norm["Errors"] == 1
        # The summary failure record must include both the failure metric
        # and Invocations (the denominator for failure-rate).
        fail = next(r for r in records if r.get("event") == "pipeline_failed")
        assert fail["InvocationFailures"] == 1.0
        assert fail["Invocations"] == 1.0


# ---------------------------------------------------------------------------
# EMF metric-contract tests — the dashboard YAML hard-codes these names.
# If a refactor renames RowsIngested, this test must fail before the
# dashboard goes blank in production.
# ---------------------------------------------------------------------------


class TestEMFContract:
    REQUIRED_STAGES = {"read", "normalise", "write", "upload"}

    def test_each_stage_emits_latency_and_errors(self, s3_with_csv, capsys):
        _, bucket = s3_with_csv
        pipeline.handler(_event(bucket, "raw/in.csv"), None)
        records = self._records(capsys)
        # Per-stage records: pull Stage from dimension fields.
        stages = {r["Stage"] for r in records if "Latency" in r}
        assert self.REQUIRED_STAGES.issubset(stages)

    def test_happy_path_emits_row_count_summary(self, s3_with_csv, capsys):
        _, bucket = s3_with_csv
        pipeline.handler(_event(bucket, "raw/in.csv"), None)
        records = self._records(capsys)
        summary = next(r for r in records if r.get("event") == "pipeline_done")
        assert summary["RowsIngested"] == 2.0
        assert summary["RowsDropped"] == 1.0
        assert summary["Invocations"] == 1.0

    def test_records_carry_request_id(self, s3_with_csv, capsys):
        _, bucket = s3_with_csv
        pipeline.handler(_event(bucket, "raw/in.csv"), None)
        records = self._records(capsys)
        # Every stage record + the summary should carry a request_id.
        with_id = [r for r in records if "request_id" in r]
        assert len(with_id) >= 5
        # All the same id within one invocation — that's the correlation key.
        assert len({r["request_id"] for r in with_id}) == 1

    def test_namespace_is_pipeline(self, s3_with_csv, capsys):
        _, bucket = s3_with_csv
        pipeline.handler(_event(bucket, "raw/in.csv"), None)
        records = self._records(capsys)
        namespaces = {
            r["_aws"]["CloudWatchMetrics"][0]["Namespace"] for r in records if "_aws" in r
        }
        assert namespaces == {"FossilRAG/Pipeline"}

    def test_request_id_is_NOT_a_dimension(self, s3_with_csv, capsys):
        """High-cardinality keys must not be CloudWatch dimensions."""
        _, bucket = s3_with_csv
        pipeline.handler(_event(bucket, "raw/in.csv"), None)
        records = self._records(capsys)
        for r in records:
            if "_aws" not in r:
                continue
            dim_keys = r["_aws"]["CloudWatchMetrics"][0]["Dimensions"][0]
            assert "request_id" not in dim_keys, (
                "request_id became a CloudWatch dimension — this will create "
                "one metric series per invocation and explode the bill."
            )

    @staticmethod
    def _records(capsys):
        out = capsys.readouterr().out
        return [json.loads(ln) for ln in out.splitlines() if ln.strip().startswith("{")]


# ---------------------------------------------------------------------------
# Anti-pattern regression tests — the broken baseline must stay broken
# so the "before/after" remains demonstrable.
# ---------------------------------------------------------------------------


class TestBrokenBaseline:
    BROKEN = ROOT / "broken" / "pipeline.py"

    def test_broken_uses_print_not_logging(self):
        src = self.BROKEN.read_text()
        assert "print(" in src
        assert "logging.getLogger" not in src

    def test_broken_swallows_exceptions(self):
        src = self.BROKEN.read_text()
        # Bare except with pass — the silent-failure bug.
        assert "except Exception:" in src
        assert "pass" in src

    def test_broken_returns_200_unconditionally(self):
        src = self.BROKEN.read_text()
        # Two `return {"statusCode": 200}` lines: one in the try, one
        # at module bottom after the except.
        assert src.count('return {"statusCode": 200}') >= 2

    def test_broken_emits_no_emf_records(self):
        src = self.BROKEN.read_text()
        assert "_aws" not in src
        assert "CloudWatchMetrics" not in src

    def test_fixed_imports_metrics_module(self):
        src = (ROOT / "pipeline.py").read_text()
        assert "from metrics import" in src
        assert "stage_timer" in src
