# IAM policies for Activity 10

Two policies. Both attach to a single role,
**`fossilrag-deployer-staging`**, that the deploy workflow assumes via
GitHub OIDC. Replace the placeholders before applying — the JSON is a
reference template, not a deployable artifact.

| File | Attaches as | Placeholders |
|---|---|---|
| [`github-oidc-trust-policy.json`](github-oidc-trust-policy.json) | the role's **trust** relationship | `123456789012` (account id), `Melvin0070/bootcamp` (GitHub `org/repo`) |
| [`staging-deploy-policy.json`](staging-deploy-policy.json) | a **permissions** policy (inline or customer-managed) | `fossilrag-staging-deploy` (bucket name) |

## Apply (one-time)

```bash
# 1. Register GitHub as an OIDC provider on the AWS account (one
#    command per account, ever — already idempotent).
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1

# 2. Create the role with the trust policy.
aws iam create-role \
  --role-name fossilrag-deployer-staging \
  --assume-role-policy-document file://github-oidc-trust-policy.json

# 3. Attach the permissions policy.
aws iam put-role-policy \
  --role-name fossilrag-deployer-staging \
  --policy-name staging-deploy \
  --policy-document file://staging-deploy-policy.json
```

## Why these specific resources

The trust policy's `sub` condition pins the role to the
**`staging` GitHub environment** — not just the repo. Even if a fork
or a feature branch tries to assume the role, the OIDC subject will
not match (`repo:.../environment:staging` only fires for runs that
bind to that environment, which only the deploy workflow does). This
is why the deploy workflow's job-level `environment: staging` is
load-bearing for security, not just for human approval.

The permissions policy is intentionally narrow: PUT/GET on the
staging bucket, ListBucket scoped to that bucket, nothing else. Even
if the role were misused, the blast radius is "rewrite a staging
artifact" — no IAM, no Secrets Manager, no production buckets.
