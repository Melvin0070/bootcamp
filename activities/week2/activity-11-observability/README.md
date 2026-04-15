# Activity 11: Add Observability to a Pipeline with Zero Monitoring

**Week:** 2 | **Day:** 11 | **Course alignment:** System Design Foundations / Claude Code in Action

## Problem Statement

A pipeline has **zero monitoring** — no metrics, no alarms, no dashboards. Failures are only discovered when downstream users report missing data.

## What to Fix

- [ ] Define **CloudWatch custom metrics** for failure rate and processing latency
- [ ] Set up **CloudWatch Alarms** that fire when failure rate > threshold
- [ ] Create a simple **CloudWatch Dashboard** with key health indicators
- [ ] Emit structured log events that CloudWatch Logs Insights can query

## Acceptance Criteria

- Failure rate and latency metrics are visible in CloudWatch
- An alarm triggers within 5 minutes of a sustained failure
- Dashboard shows last 24h of pipeline health at a glance

## PR Checklist

- [ ] Fix applied in `broken/` → working code committed
- [ ] Clear PR description (what was broken, what you changed, why)
- [ ] 2–5 min video walkthrough (before/after)

## Notes

_Add your findings, decisions, and observations here as you work._
