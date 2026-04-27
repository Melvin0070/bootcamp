#!/usr/bin/env bash
# Post-deploy smoke test against the staging bucket.
#
# Verifies that:
#   1. The artifact for this SHA exists and is non-empty.
#   2. `latest.zip` has the same git-sha metadata as the just-deployed
#      artifact (i.e. the pointer update succeeded).
#
# Exit non-zero on any mismatch — the workflow will surface it as a
# failed deploy and the staging environment will not be marked
# "deployed" in the GitHub UI.

set -euo pipefail

BUCKET="${1:?bucket required}"
SHA="${2:?git sha required}"
KEY="pipeline-${SHA}.zip"

size=$(aws s3api head-object --bucket "$BUCKET" --key "$KEY" --query ContentLength --output text)
if [[ "$size" == "0" || -z "$size" ]]; then
  echo "::error::artifact $KEY is empty or missing" >&2
  exit 1
fi

latest_sha=$(aws s3api head-object --bucket "$BUCKET" --key "latest.zip" --query "Metadata.\"git-sha\"" --output text)
if [[ "$latest_sha" != "$SHA" ]]; then
  echo "::error::latest.zip points at $latest_sha, expected $SHA" >&2
  exit 1
fi

echo "smoke_ok sha=${SHA} size=${size}"
