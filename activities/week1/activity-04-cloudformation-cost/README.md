# Activity 4: Reduce AWS Costs in an Over-Provisioned CloudFormation Stack

**Week:** 1 | **Day:** 4 | **Course alignment:** AWS Technical Essentials

## Problem Statement

A CloudFormation template provisions resources that are massively over-provisioned:
- Single **`m5.4xlarge` EC2 instance** running 24/7 with no scaling policy → **$552.96/mo**
- No **lifecycle hooks** — instances are killed mid-request during scale-in
- No **cost tags** — Finance cannot attribute spend to any team or project
- SSH open to `0.0.0.0/0` — unnecessary attack surface
- Hardcoded AMI ID — template breaks outside us-east-1

## What Was Fixed

| # | Fix | Before | After |
|---|-----|--------|-------|
| 1 | Instance family | `m5.4xlarge` hardcoded ($0.768/hr) | `t3.medium` via Parameter ($0.0416/hr) — 18× cheaper |
| 2 | Scaling | Single `AWS::EC2::Instance`, no ASG | `AutoScalingGroup` (min 0, max 4) + target tracking (CPU 60%) + scheduled scale-to-zero overnight |
| 3 | Lifecycle | No hook — instances killed immediately | `LifecycleHook` on `EC2_INSTANCE_TERMINATING`, 300s heartbeat, SNS+SQS notification |
| 4 | Cost tags | No tags | `Project`, `Environment`, `Owner` on every resource; `PropagateAtLaunch: true` on ASG |
| 5 | Security | SSH port 22 open to internet | SSH removed; SSM Session Manager via `AmazonSSMManagedInstanceCore` managed policy |
| 6 | AMI | Hardcoded `ami-0c02fb55956c7d316` | SSM Parameter `LatestAmiId` resolves latest Amazon Linux 2 per region |

## Cost Impact

| Scenario | Monthly cost |
|----------|-------------|
| **Before** (m5.4xlarge 24/7) | **$552.96** |
| After — dev (t3.medium, scale-to-zero nights + weekends) | **~$9.98** |
| After — prod (t3.large, always-on 2 instances) | **~$59.90** |

**Peak saving for a dev workload: ~98%**

## Acceptance Criteria

- [x] Stack scales down to near-zero during off-peak hours (ScheduledScaleDown → DesiredCapacity=0)
- [x] All resources tagged with `Project`, `Environment`, `Owner`
- [x] No hardcoded instance sizes — `InstanceType` is a Parameter with `AllowedValues`
- [x] `m5.4xlarge` excluded from `AllowedValues`
- [x] Lifecycle hook on `EC2_INSTANCE_TERMINATING` with 300s drain window

## PR Checklist

- [x] Broken template preserved in `broken/template.yaml` with ❌ bug annotations
- [x] Fixed template at root `template.yaml` with ✅ fix annotations
- [x] 30 assertions across 5 test classes — all passing
- [x] Architecture doc with before/after diagrams, cost table, trade-off table, AWS setup guide
- [x] All config via `.env.example` (no hardcoded values in resources)
- [ ] 2–5 min video walkthrough (before/after)

## PR Evidence

| Criterion | Evidence |
|-----------|----------|
| Code Correctness | ASG min/max/desired parameters; lifecycle hook HeartbeatTimeout=300s; ScheduledScaleDown sets DesiredCapacity=0; 30 tests pass |
| Problem Solving & Architecture | t3 vs m5 trade-off table; 98% cost reduction quantified; lifecycle hook drain flow diagrammed; Shopify/e-commerce real-world examples |
| Code Quality | Zero hardcoded values in Resources; all configuration in Parameters; SSM-resolved AMI; IMDSv2 enforced |
| PR Description | Before/after ASCII diagrams; 6-row fix table; cost comparison table; scheduled cron expressions explained |
| Completeness | 22 tests; docs/architecture.md; .env.example; AWS console setup guide; Cost Explorer activation steps |

## Running the Tests

```bash
cd activities/week1/activity-04-cloudformation-cost

pip install -r requirements-dev.txt

pytest tests/ -v
```

Expected output:
```
tests/test_template.py::TestBrokenTemplateAntiPatterns::test_uses_single_ec2_instance_not_asg PASSED
tests/test_template.py::TestBrokenTemplateAntiPatterns::test_instance_type_is_hardcoded_m5_4xlarge PASSED
tests/test_template.py::TestBrokenTemplateAntiPatterns::test_no_parameters_block PASSED
tests/test_template.py::TestBrokenTemplateAntiPatterns::test_no_lifecycle_hooks PASSED
tests/test_template.py::TestBrokenTemplateAntiPatterns::test_no_cost_tags_on_ec2_instance PASSED
tests/test_template.py::TestBrokenTemplateAntiPatterns::test_no_auto_scaling_policies PASSED
tests/test_template.py::TestFixedTemplateStructure::test_has_auto_scaling_group PASSED
tests/test_template.py::TestFixedTemplateStructure::test_has_no_standalone_ec2_instance PASSED
tests/test_template.py::TestFixedTemplateStructure::test_has_launch_template PASSED
tests/test_template.py::TestFixedTemplateStructure::test_has_lifecycle_hook PASSED
tests/test_template.py::TestFixedTemplateStructure::test_has_scaling_policy PASSED
tests/test_template.py::TestFixedTemplateStructure::test_has_scheduled_scale_down PASSED
tests/test_template.py::TestFixedTemplateStructure::test_has_outputs_block PASSED
tests/test_template.py::TestParameterization::test_instance_type_is_a_parameter PASSED
tests/test_template.py::TestParameterization::test_instance_type_has_allowed_values PASSED
tests/test_template.py::TestParameterization::test_m5_4xlarge_is_not_allowed PASSED
tests/test_template.py::TestParameterization::test_launch_template_instance_type_is_reference_not_string PASSED
tests/test_template.py::TestParameterization::test_environment_is_a_parameter PASSED
tests/test_template.py::TestParameterization::test_vpc_and_subnets_are_parameters PASSED
tests/test_template.py::TestCostTags::test_all_taggable_resources_have_project_tag PASSED
tests/test_template.py::TestCostTags::test_all_taggable_resources_have_environment_tag PASSED
tests/test_template.py::TestCostTags::test_all_taggable_resources_have_owner_tag PASSED
tests/test_template.py::TestCostTags::test_asg_tags_propagate_to_instances PASSED
tests/test_template.py::TestCostTags::test_launch_template_has_instance_tag_specifications PASSED
tests/test_template.py::TestScalingConfig::test_min_size_allows_scale_to_zero PASSED
tests/test_template.py::TestScalingConfig::test_scheduled_scale_down_sets_desired_to_zero PASSED
tests/test_template.py::TestScalingConfig::test_lifecycle_hook_fires_on_terminating PASSED
tests/test_template.py::TestScalingConfig::test_lifecycle_hook_heartbeat_timeout_is_positive PASSED
tests/test_template.py::TestScalingConfig::test_scaling_policy_type_is_target_tracking PASSED
tests/test_template.py::TestScalingConfig::test_has_cloudwatch_alarm PASSED
```

## Notes

### Why t3 over m5?

`t3` instances use burstable CPU credits. They accumulate credits when idle
and spend them during spikes. For a web server that peaks at ~10% CPU with
occasional spikes, this is ideal — you get headroom when needed without paying
for 16 vCPU cores 24/7.

Use `m5` (fixed performance) for: sustained 100% CPU workloads like video
encoding, ML training, or database servers where CPU credit exhaustion would
cause a drop in performance at exactly the wrong moment.

### Why lifecycle hooks matter

Without a lifecycle hook, AWS terminates an instance in seconds. Any HTTP
request in flight is dropped. With a `HeartbeatTimeout: 300` hook, the
instance has 5 minutes to finish requests, flush caches, and close DB
connections before termination proceeds. The instance calls
`aws autoscaling complete-lifecycle-action --lifecycle-action-result CONTINUE`
when ready, or the timeout fires `DefaultResult: CONTINUE` automatically.

### Scheduled vs reactive scaling

These are complementary, not competing:
- **Scheduled** handles known off-peak windows (nights, weekends) predictably
- **Target tracking** handles intraday traffic variation reactively

Together they cover both the predictable baseline cost reduction and the
unpredictable traffic spike handling.
