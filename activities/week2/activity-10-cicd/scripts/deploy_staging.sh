#!/usr/bin/env bash
# Idempotent staging deploy.
#
# Invoked from .github/workflows/deploy.yml AFTER OIDC has assumed the
# fossilrag-deployer-staging role. AWS credentials in the environment
# are short-lived (≤1 h) tokens — no long-lived access keys anywhere.
#
# Args:
#   $1  artifact path (zip produced by the build job)
#   $2  staging bucket name (e.g. fossilrag-staging-deploy)
#   $3  git sha — used as the immutable artifact key suffix
#
# Idempotency:
#   The artifact is uploaded to s3://$BUCKET/pipeline-$SHA.zip. Re-runs
#   on the same SHA are no-ops; re-runs on a new SHA upload a new
#   immutable object and update the `latest` pointer atomically.

set -euo pipefail

ARTIFACT="${1:?artifact path required}"
BUCKET="${2:?bucket required}"
SHA="${3:?git sha required}"

if [[ ! -f "$ARTIFACT" ]]; then
  echo "::error::artifact $ARTIFACT does not exist" >&2
  exit 1
fi

KEY="pipeline-${SHA}.zip"

echo "::group::upload artifact"
aws s3 cp "$ARTIFACT" "s3://${BUCKET}/${KEY}" \
  --metadata "git-sha=${SHA},deployed-by=github-actions"
echo "::endgroup::"

echo "::group::update latest pointer"
aws s3 cp "s3://${BUCKET}/${KEY}" "s3://${BUCKET}/latest.zip" \
  --metadata-directive REPLACE \
  --metadata "git-sha=${SHA},deployed-by=github-actions"
echo "::endgroup::"

echo "deploy_ok sha=${SHA} bucket=${BUCKET} key=${KEY}"
