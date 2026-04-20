"""
CloudFormation template tests for activity-04-cloudformation-cost.

Tests are pure Python + PyYAML — no AWS credentials required.
They validate structural properties of both the broken and fixed templates,
acting as living documentation of what was wrong and what was fixed.
"""

import yaml
import pytest
from pathlib import Path

# ---------------------------------------------------------------------------
# Custom YAML loader that handles CloudFormation intrinsic function tags
# (!Ref, !Sub, !GetAtt, !If, etc.) by wrapping them in plain dicts.
# ---------------------------------------------------------------------------
class _CFLoader(yaml.SafeLoader):
    pass


def _cf_tag(loader, tag_suffix, node):
    """Deserialise any !Tag as {'Tag': <value>} so the rest is plain Python."""
    if isinstance(node, yaml.ScalarNode):
        return {tag_suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {tag_suffix: loader.construct_sequence(node, deep=True)}
    return {tag_suffix: loader.construct_mapping(node, deep=True)}


_CFLoader.add_multi_constructor("!", _cf_tag)

ROOT = Path(__file__).parent.parent
BROKEN_PATH = ROOT / "broken" / "template.yaml"
FIXED_PATH = ROOT / "template.yaml"


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.load(fh, Loader=_CFLoader)


def _resources(tmpl: dict) -> dict:
    return tmpl.get("Resources", {})


def _resource_types(tmpl: dict) -> list[str]:
    return [r["Type"] for r in _resources(tmpl).values()]


def _tags_for(resource: dict) -> list[str]:
    """Return the list of tag Key strings for a resource, handling ASG tag format."""
    props = resource.get("Properties", {})
    tags = props.get("Tags", [])
    return [t.get("Key") for t in tags if isinstance(t, dict)]


REQUIRED_TAGS = {"Project", "Environment", "Owner"}

# Resources that support the Tags property in CloudFormation.
# ASG uses a slightly different tag structure (PropagateAtLaunch), handled separately.
TAGGABLE_TYPES = {
    "AWS::AutoScaling::AutoScalingGroup",
    "AWS::EC2::SecurityGroup",
    "AWS::EC2::LaunchTemplate",  # tags live in TagSpecifications, tested separately
    "AWS::SNS::Topic",
    "AWS::SQS::Queue",
    "AWS::CloudWatch::Alarm",
    "AWS::IAM::Role",
    # AWS::AutoScaling::LifecycleHook does NOT support a Tags property in CloudFormation.
}


# ===========================================================================
# TestBrokenTemplateAntiPatterns
# Regression: confirm the broken template has the exact anti-patterns we fixed.
# If these fail it means the broken template was altered — preserve it as-is.
# ===========================================================================
class TestBrokenTemplateAntiPatterns:

    @pytest.fixture(scope="class")
    def broken(self):
        return _load(BROKEN_PATH)

    def test_uses_single_ec2_instance_not_asg(self, broken):
        """Broken template uses a static EC2::Instance — the root cause of idle cost."""
        types = _resource_types(broken)
        assert "AWS::EC2::Instance" in types, "Expected AWS::EC2::Instance in broken template"
        assert "AWS::AutoScaling::AutoScalingGroup" not in types

    def test_instance_type_is_hardcoded_m5_4xlarge(self, broken):
        """The oversized instance type is directly in Properties, not a Parameter reference."""
        for res in _resources(broken).values():
            if res["Type"] == "AWS::EC2::Instance":
                itype = res["Properties"].get("InstanceType")
                assert isinstance(itype, str), "InstanceType should be a raw string (hardcoded)"
                assert itype == "m5.4xlarge", f"Expected m5.4xlarge, got {itype}"

    def test_no_parameters_block(self, broken):
        """Broken template has no Parameters — everything is hardcoded in Resources."""
        assert "Parameters" not in broken or broken["Parameters"] is None

    def test_no_lifecycle_hooks(self, broken):
        """No lifecycle hook means instances are killed mid-request during scale-in."""
        assert "AWS::AutoScaling::LifecycleHook" not in _resource_types(broken)

    def test_no_cost_tags_on_ec2_instance(self, broken):
        """No Project/Environment/Owner tags — AWS Cost Explorer cannot attribute spend."""
        for res in _resources(broken).values():
            if res["Type"] == "AWS::EC2::Instance":
                tag_keys = _tags_for(res)
                for required in REQUIRED_TAGS:
                    assert required not in tag_keys, (
                        f"Broken template should NOT have '{required}' tag on EC2 instance"
                    )

    def test_no_auto_scaling_policies(self, broken):
        """No scaling policies — capacity is permanently fixed."""
        assert "AWS::AutoScaling::ScalingPolicy" not in _resource_types(broken)
        assert "AWS::AutoScaling::ScheduledAction" not in _resource_types(broken)


# ===========================================================================
# TestFixedTemplateStructure
# Verify all four required fixes are present in the fixed template.
# ===========================================================================
class TestFixedTemplateStructure:

    @pytest.fixture(scope="class")
    def fixed(self):
        return _load(FIXED_PATH)

    def test_has_auto_scaling_group(self, fixed):
        """✅ FIX 2: ASG replaces single EC2 instance."""
        assert "AWS::AutoScaling::AutoScalingGroup" in _resource_types(fixed)

    def test_has_no_standalone_ec2_instance(self, fixed):
        """Fixed template uses LaunchTemplate + ASG, never a bare EC2::Instance."""
        assert "AWS::EC2::Instance" not in _resource_types(fixed)

    def test_has_launch_template(self, fixed):
        """LaunchTemplate is the ASG launch config — holds instance type + AMI."""
        assert "AWS::EC2::LaunchTemplate" in _resource_types(fixed)

    def test_has_lifecycle_hook(self, fixed):
        """✅ FIX 3: Lifecycle hook present for graceful scale-in."""
        assert "AWS::AutoScaling::LifecycleHook" in _resource_types(fixed)

    def test_has_scaling_policy(self, fixed):
        """✅ FIX 2: Reactive scaling policy (target tracking) present."""
        assert "AWS::AutoScaling::ScalingPolicy" in _resource_types(fixed)

    def test_has_scheduled_scale_down(self, fixed):
        """✅ FIX 2: At least one ScheduledAction present (scale-to-zero overnight)."""
        assert "AWS::AutoScaling::ScheduledAction" in _resource_types(fixed)

    def test_has_outputs_block(self, fixed):
        """Outputs block allows downstream stacks to import ASG name and SG ID."""
        assert "Outputs" in fixed
        assert len(fixed["Outputs"]) >= 2


# ===========================================================================
# TestParameterization
# Verify no hardcoded instance type — all configuration comes from Parameters.
# ===========================================================================
class TestParameterization:

    @pytest.fixture(scope="class")
    def fixed(self):
        return _load(FIXED_PATH)

    def test_instance_type_is_a_parameter(self, fixed):
        """InstanceType must be in the Parameters block, not hardcoded in Resources."""
        params = fixed.get("Parameters", {})
        assert "InstanceType" in params, "InstanceType must be a CloudFormation Parameter"

    def test_instance_type_has_allowed_values(self, fixed):
        """AllowedValues constraint prevents accidental use of oversized instances."""
        param = fixed["Parameters"]["InstanceType"]
        assert "AllowedValues" in param, "InstanceType Parameter must have AllowedValues"
        allowed = param["AllowedValues"]
        assert len(allowed) >= 1

    def test_m5_4xlarge_is_not_allowed(self, fixed):
        """m5.4xlarge must be explicitly excluded — it was the source of $552/mo waste."""
        param = fixed["Parameters"]["InstanceType"]
        allowed = param.get("AllowedValues", [])
        assert "m5.4xlarge" not in allowed, (
            "m5.4xlarge must NOT appear in InstanceType AllowedValues"
        )

    def test_launch_template_instance_type_is_reference_not_string(self, fixed):
        """LaunchTemplateData.InstanceType must be a !Ref, not a raw string."""
        for res in _resources(fixed).values():
            if res["Type"] == "AWS::EC2::LaunchTemplate":
                data = res["Properties"].get("LaunchTemplateData", {})
                itype = data.get("InstanceType")
                assert isinstance(itype, dict), (
                    f"LaunchTemplateData.InstanceType should be a !Ref dict, got: {itype!r}"
                )

    def test_environment_is_a_parameter(self, fixed):
        """Environment parameter allows the same template to deploy to dev/staging/prod."""
        assert "Environment" in fixed.get("Parameters", {})

    def test_vpc_and_subnets_are_parameters(self, fixed):
        """Network config must be parameters so the template works in any VPC."""
        params = fixed.get("Parameters", {})
        assert "VpcId" in params
        assert "SubnetIds" in params


# ===========================================================================
# TestCostTags
# Verify every taggable resource carries Project, Environment, Owner.
# ===========================================================================
class TestCostTags:

    @pytest.fixture(scope="class")
    def fixed(self):
        return _load(FIXED_PATH)

    def _taggable_resources(self, fixed):
        return {
            name: res
            for name, res in _resources(fixed).items()
            if res["Type"] in TAGGABLE_TYPES
        }

    def test_all_taggable_resources_have_project_tag(self, fixed):
        for name, res in self._taggable_resources(fixed).items():
            keys = _tags_for(res)
            assert "Project" in keys, f"Resource {name} ({res['Type']}) missing 'Project' tag"

    def test_all_taggable_resources_have_environment_tag(self, fixed):
        for name, res in self._taggable_resources(fixed).items():
            keys = _tags_for(res)
            assert "Environment" in keys, (
                f"Resource {name} ({res['Type']}) missing 'Environment' tag"
            )

    def test_all_taggable_resources_have_owner_tag(self, fixed):
        for name, res in self._taggable_resources(fixed).items():
            keys = _tags_for(res)
            assert "Owner" in keys, f"Resource {name} ({res['Type']}) missing 'Owner' tag"

    def test_asg_tags_propagate_to_instances(self, fixed):
        """ASG tags must have PropagateAtLaunch=true so EC2 instances inherit them."""
        for res in _resources(fixed).values():
            if res["Type"] == "AWS::AutoScaling::AutoScalingGroup":
                tags = res.get("Properties", {}).get("Tags", [])
                cost_tags = [t for t in tags if t.get("Key") in REQUIRED_TAGS]
                assert len(cost_tags) >= 3, "ASG must propagate all three cost tags"
                for tag in cost_tags:
                    assert tag.get("PropagateAtLaunch") is True, (
                        f"ASG tag '{tag['Key']}' must have PropagateAtLaunch: true"
                    )

    def test_launch_template_has_instance_tag_specifications(self, fixed):
        """Tags on LaunchTemplate TagSpecifications propagate to launched instances."""
        for res in _resources(fixed).values():
            if res["Type"] == "AWS::EC2::LaunchTemplate":
                data = res.get("Properties", {}).get("LaunchTemplateData", {})
                tag_specs = data.get("TagSpecifications", [])
                instance_specs = [s for s in tag_specs if s.get("ResourceType") == "instance"]
                assert len(instance_specs) >= 1, (
                    "LaunchTemplate must have TagSpecifications for ResourceType: instance"
                )


# ===========================================================================
# TestScalingConfig
# Verify the scaling setup allows scale-to-zero and graceful termination.
# ===========================================================================
class TestScalingConfig:

    @pytest.fixture(scope="class")
    def fixed(self):
        return _load(FIXED_PATH)

    def test_min_size_allows_scale_to_zero(self, fixed):
        """MinSize Parameter default must be 0 to allow full scale-to-zero overnight."""
        min_param = fixed.get("Parameters", {}).get("MinSize", {})
        default = min_param.get("Default")
        assert str(default) == "0", (
            f"MinSize default should be 0 to enable scale-to-zero, got: {default!r}"
        )

    def test_scheduled_scale_down_sets_desired_to_zero(self, fixed):
        """The scale-down ScheduledAction must drive DesiredCapacity to 0."""
        scale_down_found = False
        for res in _resources(fixed).values():
            if res["Type"] == "AWS::AutoScaling::ScheduledAction":
                desired = res.get("Properties", {}).get("DesiredCapacity")
                if desired == 0:
                    scale_down_found = True
                    break
        assert scale_down_found, (
            "No ScheduledAction with DesiredCapacity=0 found — scale-to-zero not configured"
        )

    def test_lifecycle_hook_fires_on_terminating(self, fixed):
        """Lifecycle hook must intercept EC2_INSTANCE_TERMINATING, not LAUNCHING."""
        for res in _resources(fixed).values():
            if res["Type"] == "AWS::AutoScaling::LifecycleHook":
                transition = res.get("Properties", {}).get("LifecycleTransition")
                assert transition == "autoscaling:EC2_INSTANCE_TERMINATING", (
                    f"LifecycleTransition should be EC2_INSTANCE_TERMINATING, got: {transition!r}"
                )

    def test_lifecycle_hook_heartbeat_timeout_is_positive(self, fixed):
        """HeartbeatTimeout must be > 0 to give instances time to drain."""
        for res in _resources(fixed).values():
            if res["Type"] == "AWS::AutoScaling::LifecycleHook":
                timeout = res.get("Properties", {}).get("HeartbeatTimeout", 0)
                assert isinstance(timeout, int) and timeout > 0, (
                    f"HeartbeatTimeout must be a positive integer, got: {timeout!r}"
                )

    def test_scaling_policy_type_is_target_tracking(self, fixed):
        """Target tracking is preferred over step scaling — no manual threshold tuning."""
        for res in _resources(fixed).values():
            if res["Type"] == "AWS::AutoScaling::ScalingPolicy":
                policy_type = res.get("Properties", {}).get("PolicyType")
                assert policy_type == "TargetTrackingScaling", (
                    f"PolicyType should be TargetTrackingScaling, got: {policy_type!r}"
                )

    def test_has_cloudwatch_alarm(self, fixed):
        """CloudWatch alarm provides visibility even when target tracking handles scaling."""
        assert "AWS::CloudWatch::Alarm" in _resource_types(fixed)
