"""
Behaviour tests for the EMF emitter.

The whole point of EMF is that you can verify the metric shape by
parsing the captured stdout line — no AWS, no moto, no network.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import metrics  # noqa: E402


def parse_record(stream: io.StringIO) -> dict:
    line = stream.getvalue().strip().splitlines()[-1]
    return json.loads(line)


class TestEmit:
    def test_record_has_aws_block(self):
        s = io.StringIO()
        metrics.emit({"Foo": (1.0, "Count")}, stream=s)
        rec = parse_record(s)
        assert "_aws" in rec
        assert "Timestamp" in rec["_aws"]
        assert "CloudWatchMetrics" in rec["_aws"]

    def test_namespace_defaults_to_pipeline(self):
        s = io.StringIO()
        metrics.emit({"Foo": (1.0, "Count")}, stream=s)
        rec = parse_record(s)
        assert rec["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "FossilRAG/Pipeline"

    def test_dimensions_become_top_level_fields(self):
        s = io.StringIO()
        metrics.emit(
            {"Latency": (12.3, "Milliseconds")},
            dimensions={"Stage": "read"},
            stream=s,
        )
        rec = parse_record(s)
        assert rec["Stage"] == "read"
        # And the dimension key is registered with CloudWatch.
        assert rec["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [["Stage"]]

    def test_metric_values_are_top_level(self):
        s = io.StringIO()
        metrics.emit({"Latency": (12.5, "Milliseconds")}, stream=s)
        rec = parse_record(s)
        assert rec["Latency"] == 12.5

    def test_metric_definitions_include_unit(self):
        s = io.StringIO()
        metrics.emit({"Latency": (12.5, "Milliseconds")}, stream=s)
        rec = parse_record(s)
        defs = rec["_aws"]["CloudWatchMetrics"][0]["Metrics"]
        assert {"Name": "Latency", "Unit": "Milliseconds"} in defs

    def test_properties_are_present_but_not_metric_defs(self):
        s = io.StringIO()
        metrics.emit(
            {"Foo": (1.0, "Count")},
            properties={"request_id": "abc-123", "event": "test"},
            stream=s,
        )
        rec = parse_record(s)
        assert rec["request_id"] == "abc-123"
        assert rec["event"] == "test"
        # request_id must NOT be registered as a CloudWatch dimension —
        # that would create one metric series per invocation.
        dims = rec["_aws"]["CloudWatchMetrics"][0]["Dimensions"][0]
        assert "request_id" not in dims

    def test_emit_returns_the_record(self):
        s = io.StringIO()
        rec = metrics.emit({"Foo": (1.0, "Count")}, stream=s)
        assert rec == parse_record(s)

    def test_single_line_json(self):
        """EMF requires one record per line; assert no embedded newlines."""
        s = io.StringIO()
        metrics.emit(
            {"Foo": (1.0, "Count")},
            properties={"big": "x" * 1000},
            stream=s,
        )
        line = s.getvalue().strip()
        assert "\n" not in line
        json.loads(line)  # round-trip parses


class TestStageTimer:
    def test_success_emits_zero_errors(self, capsys):
        with metrics.stage_timer("read"):
            pass
        captured = capsys.readouterr()
        rec = json.loads(captured.out.strip().splitlines()[-1])
        assert rec["Errors"] == 0
        assert rec["Stage"] == "read"
        assert rec["success"] is True
        assert rec["Latency"] >= 0

    def test_failure_emits_one_error_then_reraises(self, capsys):
        with pytest.raises(ValueError, match="boom"):
            with metrics.stage_timer("normalise"):
                raise ValueError("boom")
        captured = capsys.readouterr()
        rec = json.loads(captured.out.strip().splitlines()[-1])
        assert rec["Errors"] == 1
        assert rec["Stage"] == "normalise"
        assert rec["success"] is False

    def test_request_id_propagates_to_properties(self, capsys):
        with metrics.stage_timer("upload", request_id="req-xyz"):
            pass
        captured = capsys.readouterr()
        rec = json.loads(captured.out.strip().splitlines()[-1])
        assert rec["request_id"] == "req-xyz"

    def test_latency_is_in_milliseconds(self, capsys):
        import time

        with metrics.stage_timer("read"):
            time.sleep(0.02)
        captured = capsys.readouterr()
        rec = json.loads(captured.out.strip().splitlines()[-1])
        # 20 ms slept, generous bounds to avoid CI flakes.
        assert rec["Latency"] >= 15
        assert rec["Latency"] < 1000
