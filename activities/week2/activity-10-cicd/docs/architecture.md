# Activity 10 — CI/CD architecture

## Before: zero automation

```
   developer laptop
   ───────────────
        │
        │ (1) edits pipeline.py
        │ (2) tests "by running it once"
        │ (3) git push origin main      ← direct push, no PR
        │ (4) ssh into bastion
        │ (5) scp pipeline.py
        │ (6) restart cron
        ▼
    EC2 cron host
        │
        ▼
        S3 (prod bucket — no separation)
```

Failure modes that have actually happened on this code:

- A typo in a column rename merged on a Friday afternoon and was
  caught by the downstream consumer the following Monday.
- Two contributors force-pushed over each other's work in the same
  morning. The second push silently deleted the first's changes.
- The deployer's long-lived AWS access key was committed in a
  different repo by accident (Activity 9 territory) and one of the
  questions during the post-mortem was "what could that key access?"
  — the answer was "everything in prod" because there was no
  per-environment role.

## After: PR gate + automated staging deploy

```
                          ┌──── Pull Request ─────┐
   developer ─── push ──▶ │   activity-10-ci.yml  │
                          │   ├── lint (ruff)     │
                          │   ├── test (3.11)     │
                          │   └── test (3.12)     │
                          │       └── ci-passed   │ ◄── required status check
                          └────────────┬──────────┘
                                       │ green + reviewer approves
                                       ▼
                                ┌──────────────┐
                                │  merge main  │
                                └──────┬───────┘
                                       │ push:branches:[main]
                                       ▼
                       ┌────── activity-10-deploy.yml ──────┐
                       │  build (zip + import smoke)        │
                       │     └─▶ deploy-staging             │
                       │           ├─ environment:staging   │ ◄── reviewer + 30 s wait
                       │           ├─ OIDC → IAM role       │ ◄── short-lived creds
                       │           ├─ s3 cp artifact        │
                       │           ├─ s3 cp latest pointer  │
                       │           └─ smoke_staging.sh      │
                       └──────────────────┬─────────────────┘
                                          ▼
                                  S3 staging bucket
                                  (separate from prod)
```

## Trade-offs

### Two workflows, not one

A single workflow with `if: github.event_name == 'pull_request'` for
the test job and `if: github.event_name == 'push'` for deploy is more
compact, but:

- The deploy workflow needs `id-token: write` and binds to the
  `staging` environment. Putting that next to a PR-test job means a
  PR from a fork would request privileges it must not get. Splitting
  the files makes the privilege boundary obvious.
- Branch protection's required-status-check selector is per-workflow.
  Two files give two distinct entries in the UI, which is harder to
  misconfigure.

### OIDC, not access keys

GitHub-hosted runners can mint a short-lived JWT signed by GitHub's
OIDC issuer. AWS IAM trusts that issuer (one-time setup) and exchanges
the JWT for a session credential scoped to a specific role. Result:

- No long-lived AWS keys live in GitHub Secrets.
- The IAM role's trust policy pins the credential to a specific
  repo + branch + environment. A workflow in another repo cannot
  assume the role even if it copies the role ARN.
- CloudTrail records the `role-session-name` (we set it to
  `gha-activity10-<sha>`) so every deploy is auditable back to a
  commit.

The alternative — `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` in
GitHub Secrets — works but couples deploy security to "did the
secret leak?" rather than "did the OIDC trust policy match?". A
leaked OIDC role ARN is harmless on its own.

### Required reviewer on the staging environment

For a one-person bootcamp project this looks heavy. It is intentional:

- Auto-merge bots that pass CI but introduce a regression cannot
  reach AWS without a human ack.
- During an incident, the reviewer can choose *not* to approve
  pending deploys without rolling back the merge.
- It models the workflow used in real shops, which is the point of
  the bootcamp.

The 30-second wait timer is the same idea, cheaper. It buys the
"oh shit, revert that merge" window.

### Path-filtered workflows

This repo holds 12 activities. Without the `paths:` filter, a typo in
Activity 7's README would re-run Activity 10's full matrix. The
filter scopes runs to `activities/week2/activity-10-cicd/**` plus the
two workflow files themselves, which catches both code changes and
workflow changes.

### `ci-passed` aggregator job

Branch protection requires named status checks. Listing
`test (3.11)`, `test (3.12)`, `lint (ruff)` works until you add a
Python version, at which point you must remember to update the
protected branch settings. The aggregator job has a stable name and
fails iff any upstream cell fails — branch protection only ever lists
`ci-passed`.

### Coverage gate at 85 %

Coverage is a coarse signal but coverage *trend* is a useful one.
85 % is reachable on this small module without writing nonsense
tests, and it stops the "added a function with zero tests" PR.
Bumping it is a one-line change in the workflow.

## Edge cases handled

| Scenario | Behaviour |
|---|---|
| PR opened with no code changes (only README in another activity) | Workflow does not run — path filter excludes it. |
| Same SHA deployed twice | `deploy_staging.sh` is idempotent: same key, same metadata. `latest.zip` re-points to itself, no-op. |
| Two PRs merge within seconds | Deploy workflow concurrency group `activity-10-deploy-staging` serialises. The second deploy waits, no race on the `latest` pointer. |
| OIDC trust policy misconfigured | `aws-actions/configure-aws-credentials` fails the step with a clear AssumeRoleWithWebIdentity error. Job fails, environment shows red, no S3 mutation. |
| Smoke test fails after upload | Workflow exits non-zero, environment in GitHub UI shows failed deploy. The artifact remains in S3 but `latest.zip` may point at a known-bad SHA — operator runs the manual rollback (see runbook). |
| Forked PR | `pull_request` event runs CI in read-only mode (no secrets, no OIDC). Deploy never runs from a fork because the trigger is `push: branches: [main]` which forks cannot do. |

## File layout

```
activity-10-cicd/
├── broken/                  # the "before" pipeline + manual deploy
│   ├── pipeline.py
│   └── deploy.sh
├── pipeline.py              # the fixed pipeline (testable, logged)
├── scripts/
│   ├── deploy_staging.sh    # idempotent staging deploy
│   └── smoke_staging.sh     # post-deploy verification
├── iam/
│   ├── github-oidc-trust-policy.json
│   └── staging-deploy-policy.json
├── tests/
│   ├── test_pipeline.py     # business-logic + I/O tests
│   └── test_workflows.py    # YAML structure + privilege assertions
├── docs/
│   ├── architecture.md      # this file
│   ├── branch-protection.md # the GitHub-side configuration
│   └── runbook.md           # what to do when deploy goes red
└── README.md
```

The workflows themselves live at `.github/workflows/activity-10-*.yml`
at the repo root because that is the only place GitHub Actions
discovers them.
