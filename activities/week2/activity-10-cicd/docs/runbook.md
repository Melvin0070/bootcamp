# Runbook: deploy-staging is red

You merged a PR. The `activity-10-deploy-staging` workflow is failing.
This file says what to do, in order.

## 1. Identify the failing step

Open the workflow run in the Actions tab. The job tree always looks
like:

```
build → deploy-staging
              ├── configure aws credentials (OIDC)
              ├── deploy
              └── smoke test
```

The failure is in exactly one of these. Each has a different fix.

## 2. By failing step

### `build` failed

CI was green but build is red. Most often: `pip install` resolved a
new transient dependency that broke the import smoke. Re-run the
workflow once (transient registry hiccup). If still failing, revert
the merge:

```bash
git revert <merge-sha>
git push origin main
```

The revert re-triggers CI and deploy. Staging stays on the previous
artifact (the `latest.zip` pointer was never updated — the build job
runs before any S3 write).

### `configure aws credentials (OIDC)` failed

The error message is one of:

- `Not authorized to perform sts:AssumeRoleWithWebIdentity` —
  trust policy is wrong. Check `iam/github-oidc-trust-policy.json`
  matches what is attached to the role. The `sub` claim in particular
  is exact-match: `repo:Melvin0070/bootcamp:environment:staging`.
- `Could not load credentials from any providers` — the action is
  newer than the workflow expected. Pin the action SHA and retry.

No S3 mutation has happened yet — staging is unchanged.

### `deploy` failed

Half the work might be done: the artifact may have uploaded but the
`latest.zip` pointer update may have failed. Check:

```bash
aws s3 ls s3://$STAGING_BUCKET/ --recursive
aws s3api head-object --bucket $STAGING_BUCKET --key latest.zip \
  --query 'Metadata."git-sha"'
```

If `latest.zip` still points at the previous SHA, the staging deploy
is effectively a no-op — re-run the workflow once the underlying
issue is fixed.

If `latest.zip` points at the new SHA but the artifact for that SHA
is missing (very unlikely — the script uploads the artifact first),
re-upload manually:

```bash
aws s3 cp dist/pipeline-$SHA.zip s3://$STAGING_BUCKET/pipeline-$SHA.zip
```

### `smoke test` failed

The artifact is uploaded but the verification didn't pass. Two cases:

1. `artifact $KEY is empty or missing` — race with eventual
   consistency (rare in modern S3 strong-read-after-write regions).
   Re-run the smoke step.
2. `latest.zip points at $X, expected $Y` — the deploy step's two
   `s3 cp` calls did not both succeed. Roll back by pointing
   `latest.zip` at the previous known-good SHA:

```bash
PREV=$(aws s3api head-object --bucket $STAGING_BUCKET --key latest.zip \
       --query 'Metadata."git-sha"' --output text)
aws s3 cp s3://$STAGING_BUCKET/pipeline-$PREV.zip s3://$STAGING_BUCKET/latest.zip \
  --metadata-directive REPLACE \
  --metadata "git-sha=$PREV,deployed-by=manual-rollback"
```

## 3. Rollback procedure (general)

Find the previous SHA (the parent of the bad merge) and re-deploy it:

```bash
gh workflow run activity-10-deploy.yml --ref <previous-good-sha>
```

The workflow's path filter does not block manual dispatch, so this
works even if the previous SHA touched no files in the activity.

## 4. When to bypass branch protection

Almost never. The "do not allow bypassing" checkbox in branch
protection is on for a reason. The legitimate cases are:

- Reverting a merge during an incident, when CI itself is also
  broken (e.g. a registry outage). In that case use the admin
  override on the revert PR, document it in the post-mortem, and
  re-enable enforcement immediately afterwards.
- A first-time setup where the `ci-passed` check has never run
  successfully and therefore is not yet listed as a required check.
  Once the first PR has gone through, set it required and don't look
  back.

## 5. Post-incident

- File a follow-up to add a regression test that would have caught
  this case at the PR stage.
- If the failure was at deploy time, ask whether the smoke test
  should have caught it earlier — usually the answer is "yes, add
  another assertion to `smoke_staging.sh`".
- Note any gaps in this runbook itself. The runbook is part of the
  workflow; an incident that taught us something must update it.
