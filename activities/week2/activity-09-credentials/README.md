# Activity 9: Remove Hard-Coded Credentials from the Codebase

**Week:** 2 | **Day:** 9 | **Course alignment:** System Design Foundations

## Problem Statement

API keys and AWS credentials were **hard-coded in source files** — a
critical vulnerability that exposes the secrets forever in git history if
the repo ever leaves the team. GitHub research from 2022 found that
automated scanners harvest a real AWS access key from a public repo in
**under 60 seconds**.

## What to Fix

- [x] Audit the codebase — every secret literal is gone from tracked source
- [x] Replace with **environment variables** (via gitignored `.env` files)
- [x] For production: layered fallback to **AWS Secrets Manager** with TTL caching
- [x] Add `.gitignore` entries for `.env`, `*.pem`, `*.key`, `credentials`
- [x] Add `pre-commit` hooks (gitleaks + detect-secrets + detect-aws-credentials)
- [x] Document the rotation procedure and IAM policy in [`SECURITY.md`](SECURITY.md)

## Acceptance Criteria

- Zero hard-coded secrets in any tracked file (verified by tests + gitleaks) ✅
- `.env.example` documents required variables without values ✅
- Secrets Manager integration works with caching, layering, and least-privilege IAM ✅

## What Was Fixed

| # | Anti-pattern (broken) | Fix applied |
|---|---|---|
| 1 | `OPENAI_API_KEY = "sk-..."` literal | `get_secret("OPENAI_API_KEY")` — env var first, AWS Secrets Manager fallback |
| 2 | `AWS_ACCESS_KEY_ID = "AKIA..."` + `AWS_SECRET_ACCESS_KEY = "..."` literals | Removed entirely. boto3 uses the default credential provider chain → IAM role on EC2/ECS/EKS/Lambda |
| 3 | `boto3.client("s3", aws_access_key_id=...)` | `boto3.client("s3", region_name=AWS_REGION)` — no static credentials |
| 4 | `print(f"Booting with key {OPENAI_API_KEY}")` | Removed. Logs include the secret *name* and *source* (env / SM), never the value |
| 5 | Eager secret resolution at module import | `@lru_cache(1)` lazy clients; `get_secret()` only fires when actually needed |
| 6 | No `.gitignore` for `.env` | `.gitignore` excludes `.env`, `*.pem`, `*.key`, `credentials/`, `.aws/`, `.openai-key` |
| 7 | No pre-commit / CI scanning | `.pre-commit-config.yaml` with **gitleaks** + **detect-secrets** + **detect-aws-credentials** + **detect-private-key** |
| 8 | No IAM policy reference | `iam/secrets-reader-policy.json` — least-privilege, per-ARN, with `kms:ViaService` condition |
| 9 | No rotation procedure | [`SECURITY.md`](SECURITY.md) covers planned + emergency rotation, leak response, and naming convention |
| 10 | No retry/cache for SM lookups | `_Cache` with TTL (default 300 s); same secret read 1000 times → 1 SM API call |

## Architecture (one-liner)

```
get_secret(name) → cache → env var → AWS Secrets Manager → SecretNotFoundError
```

The chain is deliberate: env var first means local dev and CI can override
anything without touching AWS; SM second means production has a single
source of truth that supports rotation and audit; **no fallback to a
default value** — a missing secret raises loudly instead of silently
masking a config bug.

Full diagrams, trade-offs, edge cases, and IAM rationale in
[`docs/architecture.md`](docs/architecture.md).
Developer-facing guide (rotation, leak response, naming convention) in
[`SECURITY.md`](SECURITY.md).

## How to Run Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Expected: **29 tests passing** — 17 source-file analysis assertions, 5
operational-hygiene assertions (gitignore, .env.example, pre-commit, IAM
policy), and 7 behaviour tests against `secrets_manager.get_secret()` with
a stubbed Secrets Manager client.

The behaviour tests verify that:
- env var wins over Secrets Manager
- SM is called only on env miss
- repeated lookups hit the cache (1 SM call for N reads)
- TTL expiry triggers a re-fetch
- a missing secret raises `SecretNotFoundError`
- the secret value never appears in any log record

## How to Run Locally

```bash
cp .env.example .env
# fill in real values for OPENAI_API_KEY, DATABASE_URL, etc.
pip install -r requirements.txt pre-commit
pre-commit install
python -c "from config import get_openai_client; print(get_openai_client())"
```

## Layout

```
activity-09-credentials/
├── broken/config.py             # "before" — hardcoded literals + print(key)
├── config.py                    # production-grade config (no static creds)
├── secrets_manager.py           # env → SM lookup with TTL cache
├── tests/test_secrets.py        # 29 assertions
├── docs/architecture.md         # design rationale, edge cases, trade-offs
├── iam/secrets-reader-policy.json  # least-privilege IAM policy
├── .env.example                 # variable names, no values
├── .gitignore                   # excludes .env, *.pem, etc.
├── .pre-commit-config.yaml      # gitleaks + detect-secrets
├── SECURITY.md                  # developer guide: rotation, leaks
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

## PR Checklist

- [x] Zero hardcoded secrets in any tracked file (broken/ uses obviously-fake `FAKE` markers)
- [x] `.env.example` documents every variable with no real values
- [x] 29 pytest assertions cover anti-patterns, fixes, hygiene, and behaviour
- [x] `docs/architecture.md` — diagrams, trade-offs, edge cases, IAM rationale
- [x] `SECURITY.md` — rotation procedure, leak response, naming convention
- [x] Pre-commit config (gitleaks + detect-secrets + AWS-creds-detect)
- [x] Least-privilege IAM policy with per-ARN resources
- [ ] 2–5 min video walkthrough (before/after) — to add

## Notes

**Why env-first over SM-first:** local dev and CI need to override secrets
with zero AWS round-trips. SM-first would force every developer to have
read access to prod secrets, which violates least privilege. Env-first
means devs run with their own creds in their own `.env`, and SM is the
production backstop.

**Why not SSM Parameter Store:** SM costs ~$0.40/secret/month vs. SSM's
free tier, but SM supports automatic rotation (for some secret types),
finer IAM (one ARN per secret), and audit trails CloudTrail can query.
For 10-50 production secrets the cost is rounding error and the
operational gains are real.

**Why no fallback default:** `get_secret("FOO", default="")` is the
classic anti-pattern that lets production limp along on an empty key for
weeks before someone notices. Failing loudly with `SecretNotFoundError`
forces an explicit decision per call site.

**Why both gitleaks AND detect-secrets:** they catch overlapping but
non-identical sets. detect-secrets is entropy-based and excellent at
finding base64-encoded JWTs and high-entropy strings. gitleaks has a
curated rule set that catches Slack tokens, GH PATs, OpenAI keys, etc.
Running both in series is <1 s and adds defence-in-depth — a leak that
slips one is usually caught by the other.

**Why log redaction matters:** `print(api_key)` puts the secret into
CloudWatch / Datadog / stdout, which often have 90+ day retention. A key
exposed for a minute on a public log aggregator is exposed forever. The
fix logs the *name* and *source* of the secret (`event=secret_resolved
name=OPENAI_API_KEY source=env`) but never the value. A redacted
first-4-chars probe is gated behind `LOG_SECRET_PROBE=1` for the rare
case where an operator needs to confirm "which key is loaded?".

**Why fake-key markers in broken/:** the `FAKE` substring in every
literal is deliberate — it lets us teach the anti-pattern without leaking
a real-shaped key into git history. The shape (`sk-...`, `AKIA...`) is
preserved so the bug is recognisable; the value is unmistakably fake so
no security scanner false-positives.
