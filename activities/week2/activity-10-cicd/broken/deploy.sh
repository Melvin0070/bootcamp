#!/usr/bin/env bash
# Manual deploy script — BROKEN baseline for Activity 10.
#
# This is the "deploy procedure" the team has been using. Everything
# wrong with manual deploys is in this 12-line file:
#
#   - runs from the deployer's laptop with the deployer's long-lived
#     access keys (no IAM role, no OIDC, no audit trail beyond their
#     bash history)
#   - no test gate — `python -c "import pipeline"` is the only check
#     and it passes even when pytest would have failed
#   - no version pinning in the s3 cp — whatever is on disk wins
#   - no rollback plan; the previous version was overwritten in place
#   - no environment separation — STAGING and PROD share the same
#     bucket prefix because nobody set up a second one
#
# The fix replaces this with .github/workflows/deploy.yml triggered on
# merge to main, gated by environment=staging with required reviewers,
# using OIDC short-lived credentials to assume an IAM role.

set -e

echo "deploying pipeline.py to staging..."

# Anti-pattern: AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are read from
# the deployer's shell. In a CI/CD world these would be short-lived
# tokens minted by GitHub's OIDC provider for this specific run.
python -c "import pipeline"

aws s3 cp pipeline.py "s3://fossilrag-deploy/pipeline.py"

echo "done. (no smoke test, no rollback, no audit trail)"
