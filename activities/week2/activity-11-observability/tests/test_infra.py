"""
Static assertions over infra/observability.yaml.

The dashboard JSON is rendered into the CloudFormation template body
as a string; the alarms reference metric names that the pipeline emits.
If a refactor renames a metric, this test should fail BEFORE the
dashboard goes blank in production.

cfn-lint runs as a separate make/CI step (it validates against AWS's
resource schemas; we don't shell out to it from pytest).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "infra" / "observability.yaml"


# CloudFormation YAML uses !Ref / !Sub / !GetAtt etc. that PyYAML rejects
# by default. Register a SafeLoader that treats them all as opaque
# strings — we only care about structural assertions here.
class CFLoader(yaml.SafeLoader):
    pass


def _cf_constructor(loader, tag, node):  # noqa: ARG001
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_mapping(node, deep=True)


for tag in (
    "!Ref",
    "!Sub",
    "!GetAtt",
    "!If",
    "!Equals",
    "!Not",
    "!Join",
    "!Select",
    "!Split",
    "!FindInMap",
    "!And",
    "!Or",
):
    CFLoader.add_constructor(tag, lambda loader, node, t=tag: _cf_constructor(loader, t, node))


def load_template() -> dict:
    return yaml.load(TEMPLATE.read_text(), Loader=CFLoader)


# ---------------------------------------------------------------------------
# Resource shape — alarms, dashboard, composite alarm all present.
# ---------------------------------------------------------------------------


def test_template_parses():
    t = load_template()
    assert t["AWSTemplateFormatVersion"] == "2010-09-09"


# Per-stage alarms: one Errors alarm and one Latency alarm per stage.
STAGES = ("Read", "Normalise", "Write", "Upload")
STAGE_VALUES = {"Read": "read", "Normalise": "normalise", "Write": "write", "Upload": "upload"}
ERROR_ALARMS = [f"PipelineErrors{s}Alarm" for s in STAGES]
LATENCY_ALARMS = [f"PipelineLatency{s}Alarm" for s in STAGES]
ALL_METRIC_ALARMS = ERROR_ALARMS + LATENCY_ALARMS + ["PipelineEmptyOutputAlarm"]


def test_required_alarms_present():
    resources = load_template()["Resources"]
    types = {k: v["Type"] for k, v in resources.items()}
    for name in ALL_METRIC_ALARMS:
        assert types[name] == "AWS::CloudWatch::Alarm", name
    assert types["PipelineHealthCompositeAlarm"] == "AWS::CloudWatch::CompositeAlarm"
    assert types["PipelineDashboard"] == "AWS::CloudWatch::Dashboard"


def test_alarms_target_pipeline_namespace():
    resources = load_template()["Resources"]
    for name in ALL_METRIC_ALARMS:
        props = resources[name]["Properties"]
        assert props["Namespace"] == "FossilRAG/Pipeline", name


def test_every_alarm_pins_a_stage_dimension():
    """Regression for the dimensionless-alarm bug: every EMF metric is
    emitted WITH a Stage dimension, so a dimensionless alarm would watch
    a series that is never published (and, with breaching missing-data,
    sit in permanent ALARM). Each alarm must pin exactly one Stage."""
    resources = load_template()["Resources"]
    expected_value = {"PipelineEmptyOutputAlarm": "summary"}
    for s in STAGES:
        expected_value[f"PipelineErrors{s}Alarm"] = STAGE_VALUES[s]
        expected_value[f"PipelineLatency{s}Alarm"] = STAGE_VALUES[s]

    for name in ALL_METRIC_ALARMS:
        dims = resources[name]["Properties"].get("Dimensions")
        assert dims, f"{name} has no Dimensions (would watch a non-existent series)"
        assert dims == [{"Name": "Stage", "Value": expected_value[name]}], name


def test_latency_alarms_use_p99_not_average():
    """Averaging averages hides p99 spikes; each latency alarm must use
    the extended stat, not Statistic=Average."""
    resources = load_template()["Resources"]
    for name in LATENCY_ALARMS:
        props = resources[name]["Properties"]
        assert props.get("ExtendedStatistic") == "p99", name
        assert "Statistic" not in props, name  # mutually exclusive in CFN


def test_empty_output_alarm_treats_missing_data_as_breaching():
    """If the pipeline didn't run at all, that's a problem too."""
    props = load_template()["Resources"]["PipelineEmptyOutputAlarm"]["Properties"]
    assert props["TreatMissingData"] == "breaching"


def test_composite_alarm_references_each_child():
    """The AlarmRule must mention every per-stage alarm + empty-output via
    !Sub variable bindings — that's what fans them into one notification.
    Ordering is enforced implicitly by the Ref (no DependsOn → no W3005)."""
    resources = load_template()["Resources"]
    composite = resources["PipelineHealthCompositeAlarm"]
    rule_block = composite["Properties"]["AlarmRule"]
    # AlarmRule is a !Sub list: [template_string, {var_name: !Ref X, ...}]
    bindings = rule_block[1]
    assert set(bindings.values()) == set(ALL_METRIC_ALARMS)


# ---------------------------------------------------------------------------
# Dashboard body — verify metric names that pipeline.py must emit.
# ---------------------------------------------------------------------------


def test_dashboard_references_pipeline_metrics():
    body = load_template()["Resources"]["PipelineDashboard"]["Properties"]["DashboardBody"]
    # !Sub turns into a plain string after our loader; the metric names
    # live in the template literal regardless of substitution.
    for metric in (
        "Invocations",
        "InvocationFailures",
        "Errors",
        "Latency",
        "RowsIngested",
        "RowsDropped",
    ):
        assert metric in body, f"dashboard missing metric {metric}"


def test_dashboard_per_stage_panels_cover_each_stage():
    body = load_template()["Resources"]["PipelineDashboard"]["Properties"]["DashboardBody"]
    for stage in ("read", "normalise", "write", "upload"):
        assert f'"{stage}"' in body, f"dashboard missing stage {stage}"


def test_dashboard_body_is_valid_json_after_sub_strip():
    """The body uses ${AWS::Region} / ${FunctionName} placeholders. Strip
    them and confirm the rest is valid JSON so a typo never silently
    breaks the dashboard at apply-time."""
    body = load_template()["Resources"]["PipelineDashboard"]["Properties"]["DashboardBody"]
    import re

    # All ${...} placeholders in this template appear inside JSON string
    # literals (markdown body, region values, alarm ARNs), so substitute
    # with a plain token — no quotes — and the surrounding quotes stay valid.
    stripped = re.sub(r"\$\{[A-Za-z0-9_.:]+\}", "placeholder", body)
    parsed = json.loads(stripped)
    assert "widgets" in parsed
    assert len(parsed["widgets"]) >= 4  # text, invocations, errors, latency, rows, alarms
