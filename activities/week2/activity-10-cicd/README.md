# Activity 10: Add CI/CD to a Pipeline with No Automation

**Week:** 2 | **Day:** 10 | **Course alignment:** System Design Foundations

## Problem Statement

A FossilRAG ingestion pipeline ships changes by sshing into a bastion
and overwriting `pipeline.py` in place. There is no PR gate, no test
suite, no branch protection on `main`, no environment separation, and
the deployer carries long-lived AWS access keys on their laptop.

Two contributors have force-pushed over each other's work in the past
month. A typo merged on a Friday took the downstream consumer down
until Monday. The next time it goes wrong it will go wrong silently.

## What to Fix

- [x] Create a **GitHub Actions workflow** that runs lint + tests on every PR
- [x] Add a **deploy-to-staging** job that triggers after merge to `main`
- [x] Document branch protection: require CI to pass before merging
- [x] Cache `pip` dependencies for fast runs
- [x] Use **OIDC** to assume an IAM role — no long-lived AWS keys
- [x] Bind staging deploys to a GitHub `environment` with required reviewer
- [x] Add a smoke test that runs against the just-deployed artifact
- [x] Make the deploy script **idempotent** (safe to re-run on the same SHA)

## Acceptance Criteria

- Every PR triggers the test workflow automatically ✅
- Merging to `main` automatically deploys to staging ✅
- A failing test blocks the PR merge (via the `ci-passed` required check) ✅
- No long-lived AWS credentials live in the repo or in GitHub Secrets ✅

## What Was Fixed

| # | Anti-pattern (broken) | Fix |
|---|---|---|
| 1 | No tests at all | `tests/test_pipeline.py` — 17 behaviour tests against the transform, local I/O, and a moto-stubbed S3 |
| 2 | No CI | `.github/workflows/activity-10-ci.yml` — lint + matrix pytest (3.11/3.12) on every PR |
| 3 | Manual deploy via `ssh + scp` | `.github/workflows/activity-10-deploy.yml` — automated on merge to `main` |
| 4 | Long-lived AWS access keys on the deployer's laptop | OIDC role assumption via `aws-actions/configure-aws-credentials`, short-lived creds only |
| 5 | No branch protection on `main` | `docs/branch-protection.md` — exact UI settings + idempotent `gh api` command |
| 6 | No environment separation | `environment: staging` on the deploy job, with required reviewer + 30s wait timer |
| 7 | Unpinned dependencies | `requirements.txt` with bounded ranges, `requirements-dev.txt` for the test toolchain |
| 8 | `print()` everywhere | `logging.getLogger("fossilrag.pipeline")` with structured `event=` keys |
| 9 | `sys.argv[1]` with no validation | `argparse` with `--input/--bucket/--key`, refuses non-`.parquet` keys |
| 10 | Re-running the pipeline overwrites silently | Deploy script uploads to immutable `pipeline-<sha>.zip` then atomically updates `latest.zip` |
| 11 | No rollback path | `docs/runbook.md` with step-by-step recovery for each failing job |
| 12 | No coverage gate | `pytest --cov-fail-under=85` in CI |
| 13 | Workflows could pass on stale main | `Require branches to be up to date before merging` in branch protection doc |
| 14 | No way to verify the deploy actually worked | `scripts/smoke_staging.sh` runs after upload; failure is surfaced in the GitHub environment UI |
| 15 | Workflows could regress without anyone noticing | `tests/test_workflows.py` — static assertions over the YAML shape (triggers, permissions, OIDC, environment binding) |

## Architecture (one-liner)

```
PR → ci.yml (lint + pytest matrix + cov) → required check → human review → merge
merge → deploy.yml → build → environment:staging gate → OIDC → IAM role → S3 → smoke
```

Full diagram, trade-offs, and edge cases in [`docs/architecture.md`](docs/architecture.md).
Branch protection settings in [`docs/branch-protection.md`](docs/branch-protection.md).
Recovery procedures in [`docs/runbook.md`](docs/runbook.md).
IAM trust + permissions policies (templates) in [`iam/`](iam/).

## How to Run Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v --cov=. --cov-report=term-missing --cov-fail-under=85
```

Expected: **44 tests passing** —
- 22 pipeline behaviour tests (transform, local I/O, S3 upload via moto, CLI)
- 16 workflow-shape assertions (triggers, permissions, OIDC, environment binding, IAM least-privilege)
- 6 broken-baseline anti-pattern tests (the "before" file must keep demonstrating the bug)

## How to Run Locally

```bash
cp .env.example .env  # if you want to override LOG_LEVEL or AWS_REGION
pip install -r requirements.txt
python pipeline.py \
  --input data/fossils.csv \
  --bucket fossilrag-staging-deploy \
  --key data/normalised.parquet
```

## Layout

```
activity-10-cicd/
├── broken/                       # "before" — manual deploy + no tests
│   ├── pipeline.py
│   └── deploy.sh
├── pipeline.py                   # fixed pipeline (testable, logged)
├── scripts/
│   ├── deploy_staging.sh         # idempotent staging deploy
│   └── smoke_staging.sh          # post-deploy verification
├── tests/
│   ├── test_pipeline.py          # 22 behaviour assertions
│   └── test_workflows.py         # 16 workflow-shape assertions
├── docs/
│   ├── architecture.md           # before/after, trade-offs, edge cases
│   ├── branch-protection.md      # GitHub UI configuration + gh-cli command
│   └── runbook.md                # what to do when deploy goes red
├── iam/
│   ├── README.md
│   ├── github-oidc-trust-policy.json
│   └── staging-deploy-policy.json
├── pyproject.toml                # ruff + pytest config
├── requirements.txt              # runtime deps (bounded)
├── requirements-dev.txt          # + pytest, moto, ruff, pyyaml
└── README.md
```

The two GitHub Actions workflow files live at the repo root because
that is the only place GitHub Actions discovers them:

```
.github/workflows/
├── activity-10-ci.yml            # PR + push CI
└── activity-10-deploy.yml        # deploy on merge to main
```

## PR Checklist

- [x] CI workflow runs on every PR — lint, pytest matrix, coverage gate
- [x] Deploy workflow runs on merge to `main` — OIDC, environment-gated
- [x] Branch protection settings documented (the rule is not in code)
- [x] No hardcoded secrets, no long-lived AWS credentials
- [x] 44 pytest assertions cover anti-patterns, fixes, and workflow shape
- [x] `docs/architecture.md` — diagrams, trade-offs, edge cases
- [x] `docs/runbook.md` — incident response per failing step
- [x] Least-privilege IAM templates in `iam/` with placeholders
- [ ] 2–5 min video walkthrough (before/after) — to add

## Notes

**Why two workflows, not one with conditions:** the deploy workflow
needs `permissions.id-token: write` and binds to the `staging`
environment. Putting that next to a PR-test job that runs on forks
means a fork PR would request privileges it must not get. Splitting
the files makes the privilege boundary obvious to a reviewer skimming
the diff.

**Why OIDC, not access keys:** GitHub-hosted runners can mint a
short-lived JWT signed by GitHub's OIDC issuer. AWS exchanges that
JWT for a session credential pinned to a specific repo + environment.
A leaked role ARN is harmless on its own; a leaked access key is a
production incident. The trust policy's `sub` condition pins the role
to `repo:Melvin0070/bootcamp:environment:staging` so even another
workflow in the same repo cannot assume it.

**Why a `ci-passed` aggregator:** branch protection requires named
status checks. Listing `test (3.11)` and `test (3.12)` works until
you add a Python version, at which point the protected list silently
goes out of date. The aggregator has a stable name and fans the
matrix.

**Why required reviewer on staging deploys:** for a one-person
bootcamp it looks heavy, but the pattern matters. It catches the
"green PR with a regression" case (revert the merge before the
reviewer approves) and ties every deploy to a human-acknowledged
event in CloudTrail via `role-session-name=gha-activity10-<sha>`.

**Why path filters on the workflows:** this repo holds 12 activities.
Without `paths:`, a typo in Activity 7's README would re-run Activity
10's full matrix. The filter keeps CI minutes focused on the changes
that matter.

**Why coverage 85 %, not 100 %:** coverage is a coarse signal.
85 % is reachable on a small module without test theatre, and it
catches the "added a function, no test" case, which is the failure
mode coverage gates actually prevent. Tightening it is one line.
