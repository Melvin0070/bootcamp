# Activity 4: Reduce AWS Costs in an Over-Provisioned CloudFormation Stack

**Week:** 1 | **Day:** 4 | **Course alignment:** AWS Technical Essentials

## Problem Statement

A CloudFormation template provisions resources that are massively over-provisioned:
- Idle **EC2 instances** running 24/7 with no scaling policy
- No **lifecycle hooks** to terminate unused resources
- No **cost tags** for tracking spend per team/service

## What to Fix

- [ ] Replace fixed EC2 with an **Auto Scaling Group** (min/max/desired)
- [ ] Choose **right-sized instance families** for the workload
- [ ] Add **lifecycle hooks** to handle scale-in gracefully
- [ ] Tag all resources with `Project`, `Environment`, `Owner` cost tags

## Acceptance Criteria

- Stack scales down to near-zero during off-peak hours
- All resources are tagged for cost allocation
- No hardcoded instance sizes — use parameters or mappings

## PR Checklist

- [ ] Fix applied in `broken/` → working code committed
- [ ] Clear PR description (what was broken, what you changed, why)
- [ ] 2–5 min video walkthrough (before/after)

## Notes

_Add your findings, decisions, and observations here as you work._
