# Activity 9 — Secrets Management Architecture

## Problem Diagnosis

The original `config.py` placed secrets directly in the source tree:

```python
OPENAI_API_KEY = "sk-..."
AWS_ACCESS_KEY_ID = "AKIA..."
AWS_SECRET_ACCESS_KEY = "..."
print(f"Booting with key {OPENAI_API_KEY}")
boto3.client("s3", aws_access_key_id=..., aws_secret_access_key=...)
```

| Anti-pattern | Symptom | Root cause |
|---|---|---|
| Secrets in tracked source | If repo ever goes public or is cloned, secrets are exposed forever in git history | Treating secrets as code |
| `print(api_key)` at startup | Key ends up in CloudWatch / Datadog / stdout, often retained 90+ days | Logging without redaction |
| `boto3.client("s3", aws_access_key_id=...)` | IAM roles bypassed; static keys never rotate; rotation requires source edit + redeploy | Doesn't trust the credential provider chain |
| No `.gitignore` for `.env` | Developer's local `.env` accidentally committed — same exposure as hardcoding | No defence-in-depth |
| No pre-commit / CI scan | Next leak goes undetected until a security researcher reports it | No automated tripwire |
| Secrets resolved at module import | Any test that imports `config` hits Secrets Manager | Eager evaluation of expensive lookups |

GitHub research from 2022: a real AWS access key checked into a public
repo is harvested by automated scanners in **under 60 seconds**. The cost
of one mistake is full account takeover.

---

## Architecture: Before vs After

### Before (broken)

```
git repo
   │
   ├── config.py ─────▶ "sk-..." literal
   │                    "AKIA..." literal
   │                    print(API_KEY)        ◀── leaks to CloudWatch
   │                    boto3.client(s3, aws_access_key_id=...)
   ▼
deploy
   │
   ▼
production runtime ─── static credentials, no rotation, full account scope
```

### After (fixed)

```
git repo
   │
   ├── config.py        ──▶ get_secret("OPENAI_API_KEY")
   ├── secrets_manager.py    │
   ├── .env.example         (no values, only names)
   ├── .gitignore  ◀── .env, *.pem, credentials
   ├── .pre-commit-config.yaml ◀── gitleaks + detect-secrets
   └── iam/secrets-reader-policy.json ◀── least-privilege ARN list
   │
   ▼
deploy
   │
   ▼
production runtime
   │
   ├── IAM role attached to Lambda/ECS/EC2 (no static AWS keys)
   │       │
   │       └─▶ STS issues short-lived credentials, auto-rotated
   │
   ├── secrets_manager.get_secret("OPENAI_API_KEY")
   │       │
   │       │  layered lookup with TTL cache:
   │       │     1. os.environ.get("OPENAI_API_KEY")     ◀── CI override / dev .env
   │       │     2. boto3.secretsmanager.get_secret_value(
   │       │            SecretId="fossilrag/prod/openai_api_key"
   │       │        )                                    ◀── prod
   │       │     3. raise SecretNotFoundError            ◀── fail loud
   │       │
   │       └─▶ cached in-memory for 300 s
   │
   └── pre-commit + CI scanners block the next leak
```

---

## Trade-off Table

| Decision | Chosen | Alternative | Reasoning |
|---|---|---|---|
| Secret resolution chain | env → Secrets Manager → fail | Env → SSM Parameter Store → fail | SM is the AWS-recommended default for secrets; supports automatic rotation, KMS encryption, fine-grained IAM, and structured JSON. SSM is cheaper but lacks rotation. |
| Cache strategy | In-memory TTL (300 s default) | No cache, hit SM every call | SM is rate-limited at 10 k/sec/region and ~$0.05/10 k API calls. A hot loop without caching is both expensive and a self-DoS risk. |
| Cache invalidation | TTL only | Pub-sub, manual `clear_cache()` | TTL is "good enough": 300 s lag on rotation is acceptable for most secrets. Pub-sub adds infrastructure for marginal gain. |
| Default value handling | Raise `SecretNotFoundError` | Return `None` or empty string | Defaults silently mask config errors. A noisy fail forces an explicit decision per call site. |
| boto3 credentials | Default provider chain | Explicit env var lookup in code | The default chain handles every deployment shape (EC2, ECS, EKS, Lambda, local SSO) with no code changes. Explicit lookup re-introduces the bug. |
| Where boto3 is constructed | Lazily, behind `@lru_cache(1)` | At module import | Lazy means tests can `import config` without hitting AWS. Cached means we don't re-create the client on every call. |
| Pre-commit hook scope | gitleaks + detect-secrets + AWS-creds-detect | One scanner | Each catches a different superset; running both is <1 s and adds defence in depth. |
| IAM policy granularity | One ARN per secret in `Resource` | `Resource: "*"` | Wildcard grants read access to every secret in the account, including secrets created later by other services. Per-ARN is the principle-of-least-privilege baseline. |
| Logging policy | Name + source, never value; redacted probe gated by env var | Always redact / always include | Operators sometimes need to confirm "which key is loaded?" — a redacted probe (first 4 chars only) is the smallest leak that's still useful. |

---

## Edge Cases Handled

| Case | Behaviour |
|---|---|
| Env var set, SM also has value | Env wins; SM is never called |
| Env var missing, SM has value | Cached fetch from SM |
| Env var missing, SM ResourceNotFoundException | Raises `SecretNotFoundError` with the name and tried `secret_id` |
| Same secret read 1000 times in one process | One call to SM, 999 cache hits |
| Secret rotated in SM | Picked up after at most one TTL cycle (300 s default), no redeploy |
| boto3 unavailable (e.g. running in a tiny container without it) | `_from_secrets_manager` returns `None`; the env layer must satisfy the lookup |
| SM returns SecretBinary | Decoded as UTF-8 |
| SM returns a JSON object with one key | Unwrapped — caller gets the inner value |
| SM returns a JSON object with multiple keys | Returned as the raw JSON string; caller is responsible for `json.loads` |
| Tests import `config.py` | No SM call, no env var read — secrets resolved lazily on first `get_*_client()` call |
| Test runs `caplog.at_level(DEBUG)` | Secret value never appears in any log record |
| `LOG_SECRET_PROBE=1` set | First 4 chars logged at DEBUG; the rest redacted |
| `clear_cache()` called | Every entry dropped; next `get_secret` re-resolves |

---

## IAM Policy (least-privilege)

`iam/secrets-reader-policy.json` grants exactly two actions on exactly two
ARNs:

```json
{
  "Effect": "Allow",
  "Action": [
    "secretsmanager:GetSecretValue",
    "secretsmanager:DescribeSecret"
  ],
  "Resource": [
    "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:fossilrag/prod/openai_api_key-*",
    "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:fossilrag/prod/database_url-*"
  ]
}
```

The trailing `-*` matches the random 6-character suffix that AWS appends to
every secret ARN. Without it the policy would silently fail.

A second statement allows decryption via the AWS-managed Secrets Manager
KMS key, scoped to calls that come *via* Secrets Manager (`kms:ViaService`
condition). This stops the role being abused to decrypt arbitrary KMS-
protected resources.

**Adding a new secret** means appending one ARN to the `Resource` array.
**Never use `Resource: "*"`** — it grants read access to every secret in the
account, including secrets created later by other services.

---

## Pre-commit Strategy

Two scanners in series in `.pre-commit-config.yaml`:

1. **detect-secrets** (Yelp) — entropy + plugin-based detection. Catches
   things like base64-encoded secrets, JWT tokens, high-entropy strings.
2. **gitleaks** — Go-based rule engine with a curated set of provider
   patterns (GH PATs, Slack tokens, Stripe, AWS, GCP, OpenAI).

Plus three `pre-commit-hooks` from the standard library:

3. **detect-aws-credentials** — specifically AWS access key + secret pairs.
4. **detect-private-key** — RSA / DSA / EC private key blocks.
5. **check-added-large-files** — most credential wallets / keystores are
   >1 MB; this is a cheap secondary signal.

The `broken/` folder is `exclude`'d because its FAKE-marked literals would
trip both scanners.

CI runs the exact same hooks on the merge commit, so a developer who skips
their local hooks is still caught.

---

## Rotation Procedure

### Planned rotation
1. Mint a new value with the provider.
2. `aws secretsmanager put-secret-value --secret-id ... --secret-string ...`
3. Wait one TTL cycle (default 5 min). Every replica picks up the new
   value automatically — no redeploy.
4. Revoke the old value with the provider.

### Emergency rotation (leak suspected)
1. **Revoke immediately** with the provider. Everything else is forensics.
2. Generate + push the new value to SM as above.
3. **Audit CloudTrail** between the suspected leak timestamp and now.
4. Scrub git history (`git filter-repo`) — does NOT undo the leak, only
   makes it harder to find for future viewers.
5. File an incident report.
6. **Rotate every other secret on the same account / IAM role** — a
   stolen secret often comes packaged with credential-harvesting tools.

Full developer guide in [`SECURITY.md`](../SECURITY.md).

---

## Rollback Plan

The fix is non-destructive — every change is additive. Rollback paths:

1. **`secrets_manager.get_secret` regression** — re-deploy the previous
   container image. The rotation procedure works regardless of the
   resolution code, since SM is the source of truth.
2. **IAM policy too tight** — append the missing ARN to
   `iam/secrets-reader-policy.json` and re-apply via your IaC (Activity
   4's CloudFormation). Pods will pick up the new permissions on the next
   STS token refresh (≤1 hour).
3. **Pre-commit too aggressive** — temporarily relax the regex by adding
   the file to the `exclude` list with a TODO comment. Never use
   `--no-verify`; that bypasses every hook on every commit until disabled.
