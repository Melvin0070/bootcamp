# Activity 10: Add CI/CD to a Pipeline with No Automation

**Week:** 2 | **Day:** 10 | **Course alignment:** System Design Foundations

## Problem Statement

A data pipeline has **no CI/CD** — deployments are manual, tests are run locally (if at all), and there is no protection on the main branch.

## What to Fix

- [ ] Create a **GitHub Actions workflow** that runs tests on every PR
- [ ] Add a **deploy-to-staging** job that triggers after merge to main
- [ ] Add branch protection: require CI to pass before merging
- [ ] Cache dependencies for fast runs

## Acceptance Criteria

- Every PR triggers the test workflow automatically
- Merging to main automatically deploys to staging
- A failing test blocks the PR merge

## PR Checklist

- [ ] Fix applied in `broken/` → working code committed
- [ ] Clear PR description (what was broken, what you changed, why)
- [ ] 2–5 min video walkthrough (before/after)

## Notes

_Add your findings, decisions, and observations here as you work._
