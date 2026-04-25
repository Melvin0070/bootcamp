# Secure Development Guide

This document covers how secrets flow through the system, how to add a new
secret, how to rotate a leaked one, and how to keep new credentials out of
the repo. If you're touching any code that needs an API key, AWS access, or
a database password, read this first.

---

## TL;DR for new developers

1. `cp .env.example .env` and fill in real values. **`.env` is gitignored —
   never commit it.**
2. Get read access to the `fossilrag/dev/*` secrets in AWS Secrets Manager
   (ask the team lead). Locally, the env layer takes precedence so you can
   override anything for testing.
3. `pip install pre-commit && pre-commit install` — this runs `gitleaks` +
   `detect-secrets` on every commit. If a hook flags your change, **stop
   and read the message.** Most "false positives" are real bugs.
4. Never `print(api_key)`, never log a stack trace that includes the key,
   never paste a key into Slack / a GitHub comment.

---

## How secrets flow at runtime

```
                     ┌──────────────────────────────────┐
   application ─────▶│  secrets_manager.get_secret()    │
                     │                                  │
                     │  ┌─ in-memory TTL cache ────┐   │
                     │  │ name → (value, expires)  │   │
                     │  └──────────────────────────┘   │
                     │                                  │
                     │  if cache miss:                  │
                     │    1. os.environ.get(name)       │
                     │    2. boto3.secretsmanager       │
                     │         .get_secret_value(...)   │
                     │    3. raise SecretNotFoundError  │
                     └──────────────────────────────────┘
                                     │
                              ┌──────┴───────┐
                              ▼              ▼
                          env var         AWS Secrets
                       (local dev,         Manager
                        CI overrides)     (production)
```

**No secret ever lives at rest in the repo.** The only thing the codebase
contains is the *name* of the secret and the IAM ARN it lives under.

---

## Layer-by-layer rationale

| Layer | When it wins | Why |
|---|---|---|
| In-memory TTL cache | Repeated lookups within ~5 min | Avoids Secrets Manager API calls in hot paths (rate-limited at 10k/sec/region) |
| `os.environ.get(name)` | Local dev, CI, ad-hoc overrides | Zero AWS calls, zero IAM permissions needed; one-line override for testing |
| AWS Secrets Manager | Production, staging | Encrypted at rest with KMS, audited via CloudTrail, supports rotation, can be RBAC'd to specific IAM principals |
| (no fallback) | — | A missing secret raises `SecretNotFoundError` immediately. Defaults silently mask configuration errors and let production limp along on stale or wrong credentials |

---

## Adding a new secret

1. **Add the placeholder to `.env.example`** with no value:
       ```
       NEW_SECRET=
       ```
2. **Locally:** add the real value to your `.env` (gitignored).
3. **Production:** add the secret to AWS Secrets Manager:
       ```bash
       aws secretsmanager create-secret \
         --name fossilrag/prod/new_secret \
         --secret-string "real-value-here" \
         --description "What this secret is for, owner, rotation cadence"
       ```
4. **Update the IAM policy** at `iam/secrets-reader-policy.json` to include
   the new ARN under the `Resource` array.
5. **Read the secret in code:**
       ```python
       from secrets_manager import get_secret
       value = get_secret("NEW_SECRET")
       ```
6. **Commit only `.env.example` + IAM policy + code changes.** Never commit
   the real value.

---

## Rotating a secret

### Planned rotation (no leak)

1. Create the new value in your provider (e.g. an OpenAI dashboard).
2. Update the secret in Secrets Manager:
       ```bash
       aws secretsmanager put-secret-value \
         --secret-id fossilrag/prod/openai_api_key \
         --secret-string "new-value"
       ```
3. Wait one TTL cycle (default 5 min) — every running pod / Lambda picks
   up the new value automatically. No redeploy required.
4. Revoke the old value in your provider.

### Emergency rotation (leak suspected)

If a secret has been exposed (committed to a repo, posted in Slack,
screenshotted), assume it's in the hands of an attacker within minutes.

1. **Revoke immediately** in the provider — invalidate the leaked key.
   This is the only step that actually mitigates the leak; everything
   below is forensics.
2. Generate a new value, update Secrets Manager (as above).
3. **Audit CloudTrail** for unauthorized API calls between the leak
   timestamp and now. Look for unexpected source IPs, new IAM users,
   buckets or instances spun up that shouldn't exist.
4. **Scrub the secret from git history** with `git filter-repo` (force-push
   required). This does NOT undo the leak — it only makes the secret
   harder to find for future viewers.
5. **File an incident report** describing what leaked, when, blast radius,
   what was rotated, what CloudTrail showed.
6. **Rotate every other secret on the same account / IAM role** — a
   stolen secret often comes packaged with credentials harvesting tools
   that exfiltrate everything else they can reach.

---

## Why no static AWS access keys

The codebase deliberately constructs every boto3 client without explicit
`aws_access_key_id` / `aws_secret_access_key`:

```python
boto3.client("s3", region_name=AWS_REGION)
```

This makes boto3 use the **default credential provider chain**:

1. Env vars (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)
2. Shared credentials file (`~/.aws/credentials`)
3. IAM role attached to the running compute (EC2 instance profile, ECS
   task role, EKS IRSA, Lambda execution role)
4. SSO session token (`aws sso login`)

In production, layer 3 wins. The Lambda / ECS task / EC2 instance has an
IAM role attached at deploy time; boto3 picks it up automatically. **There
are no long-lived AWS credentials anywhere in the system.** Rotation is
handled by AWS itself — STS issues fresh, time-limited credentials every
time the role assumes. If a Lambda is compromised, the blast radius is
the role's permissions and the token's TTL (typically 12 hours).

In local dev, layer 1 or 2 wins, scoped to the developer's own IAM user.

---

## Pre-commit setup

```bash
pip install pre-commit
pre-commit install
```

The hooks in `.pre-commit-config.yaml`:

| Hook | What it catches |
|---|---|
| `detect-secrets` | Entropy + plugin-based: API keys, JWT tokens, private keys, base64-encoded secrets |
| `gitleaks` | Curated rules: GH PATs, Slack tokens, Stripe keys, AWS keys, GCP service accounts |
| `detect-aws-credentials` | Specifically AWS access key / secret pairs |
| `detect-private-key` | RSA / DSA / EC private key blocks |
| `check-added-large-files` | Anything >1 MB (often credentials wallets, keystores) |

If a hook blocks your commit, the message includes the file and line. **Do
not bypass with `--no-verify`** — fix the underlying issue. If the hook is
genuinely wrong (rare), add the file to `.pre-commit-config.yaml`'s
`exclude` list with a comment explaining why.

### CI enforcement

Pre-commit runs on every commit on the developer's machine. CI runs the
same hooks on the merge commit so a developer who skips the local hooks
still gets caught at the gate:

```yaml
# .github/workflows/secret-scan.yml (sketch)
- run: pip install pre-commit
- run: pre-commit run --all-files
```

A failing scan in CI blocks merge.

---

## What to do if you find a leaked secret

In source you're reviewing, in a Slack channel, in a screenshot, anywhere:

1. **Don't acknowledge the secret in writing.** Don't reply to the Slack
   message with the value, don't paste it into a ticket. Acknowledgement
   propagates the leak.
2. **Open a security incident channel** (Slack `#sec-incident-NNN` or your
   shop's equivalent) and ping the on-call engineer.
3. **Rotate immediately** following the emergency rotation flow above.
4. **File a post-incident retro** so the leak path gets fixed (was it a
   pre-commit hook gap? an unencrypted Slack DM? a screenshare?).

---

## Appendix: secret naming convention

`{project}/{env}/{purpose}` — e.g. `fossilrag/prod/openai_api_key`.

- One secret per ARN. JSON-blob secrets ("everything in one ARN") are
  tempting but make least-privilege IAM impossible — every consumer gets
  read access to every key in the blob.
- Kebab-case the purpose. Avoid abbreviations.
- Never include the value in the secret name (no
  `prod/openai/sk-1234567890`).
- Tag secrets with `Owner`, `RotationCadence`, `BlastRadius` for the
  weekly rotation audit.
