# Branch protection: required GitHub UI configuration

This activity introduces CI/CD, but **branch protection is not in code** —
GitHub stores the rule on the repository, not in a YAML file. The
workflow on its own is not enough; without a rule that *requires* the
workflow to pass, anyone with write access can still merge a red PR.

The settings below are the contract between the workflow and the
repository. They live here so the next maintainer knows what to
re-enable after a settings reset.

## Settings → Branches → Branch protection rule for `main`

| Setting | Value | Why |
|---|---|---|
| Require a pull request before merging | ✅ on | No direct pushes to main; everything goes through review. |
| Require approvals | ✅ 1 minimum | One reviewer is the bare minimum that catches typos and naming mistakes; for higher-risk repos bump to 2. |
| Dismiss stale pull request approvals when new commits are pushed | ✅ on | Stops "approve, then add a sneaky commit" attacks. |
| Require status checks to pass before merging | ✅ on | The whole point of CI. |
| Require branches to be up to date before merging | ✅ on | Prevents the "passes on its branch, breaks on main" failure mode where two compatible-looking PRs merge in a way that breaks. |
| Status checks required | `ci-passed` | Single aggregator job (see ci.yml) — adding a Python version doesn't require updating this list. |
| Require conversation resolution before merging | ✅ on | Open review comments must be addressed, not silently overridden. |
| Require signed commits | optional | Nice-to-have; turn on once the team has commit signing set up. |
| Require linear history | ✅ on | Force squash or rebase merges; keeps `git log main` readable. |
| Do not allow bypassing the above settings | ✅ on | Otherwise admins can still force-push and the rule is theatre. |
| Restrict who can push to matching branches | (admins only, optional) | Belt-and-braces with "do not allow bypassing". |
| Allow force pushes | ❌ off | Force-push to main is almost always a mistake or an attack. |
| Allow deletions | ❌ off | Same reason. |

## How to set it (web UI walk-through)

1. Repository → **Settings** → **Branches**
2. **Add rule** → branch name pattern `main`
3. Tick the boxes from the table above
4. In **Status checks** search for `ci-passed` and tick it (the entry
   only appears after the workflow has run at least once on a PR)
5. **Create**

## How to set it (gh CLI, idempotent)

```bash
gh api -X PUT \
  "repos/$OWNER/$REPO/branches/main/protection" \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["ci-passed"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": true,
  "required_conversation_resolution": true
}
JSON
```

## Settings → Environments → `staging`

The deploy workflow binds to `environment: staging`. That gives us a
second protection layer: even after CI passes and the PR merges, the
deploy job *waits* for the staging environment's rules.

| Setting | Value | Why |
|---|---|---|
| Required reviewers | 1+ team member | A human acknowledges the merge before short-lived AWS creds are minted. Pairs naturally with on-call rotations. |
| Wait timer | 30 seconds | Cheap brake; lets a freshly-merged PR be reverted before the deploy fires. |
| Deployment branches | `main` only | The role's OIDC trust policy already pins the env to main, but defence-in-depth is free here. |
| Environment secrets | `AWS_ROLE_TO_ASSUME`, `STAGING_BUCKET` | Stored on the env, not the repo, so a workflow that doesn't bind to `staging` cannot read them. |

## Why both PR-level CI *and* environment-level approval?

They protect different things:

- **CI on PR** answers "does this code work?" — tests, lint, types.
  The signal lives on the PR before merge so review is informed.
- **Environment approval on deploy** answers "should this deploy go
  out *now*?" — it catches the case where CI is green but the timing
  is bad (a freeze, an incident, a coordinated rollout). It also
  forces the role assumption to be tied to a human-acknowledged
  event, which CloudTrail records via the `role-session-name`.

Either alone is a hole. CI without environment approval means a green
PR auto-deploys at 2 AM during an incident. Environment approval
without CI means a human is asked to approve code that may not even
compile.
