# Infrastructure as Code (Terraform)

The serverless AWS stack for FossilRAG. Pinned to **Terraform ≥ 1.9** + **AWS
provider `~> 6.0`** (verified May 2026; see
[`../docs/adr/0001-foundational-decisions.md`](../docs/adr/0001-foundational-decisions.md)).

## What it provisions

| File | Resources |
|------|-----------|
| `s3.tf` | raw / silver / gold buckets — versioned, KMS-encrypted, public-access-blocked, lifecycle (raw expires at 90d; all → IA at 30d) |
| `sqs.tf` | ingest queue + DLQ (`maxReceiveCount=5`), queue policy, raw-bucket → SQS notification |
| `dynamodb.tf` | idempotency ledger + prompt-fossilization cache (PAY_PER_REQUEST + TTL) |
| `iam.tf` | least-privilege `worker` + `api` roles |
| `lambda.tf` | SQS **worker** (partial-batch-failure), **DLQ** handler, **API** (Mangum) with an alias, **provisioned concurrency + application auto-scaling** |
| `apigatewayv2.tf` | HTTP API → API Lambda |
| `opensearch.tf` | OpenSearch Serverless `VECTORSEARCH` collection + encryption/network/data policies |
| `secrets.tf` | Secrets Manager secret for the API's `DATABASE_URL` |
| `providers.tf` | provider-level `default_tags` for Cost Explorer attribution |

## Cost / honesty posture

- **Authored + `validate`/`plan`-verified at $0** (CI runs `fmt -check` + `init -backend=false` + `validate`; no apply, no creds).
- **Not emulable at $0:** Bedrock and OpenSearch Serverless aren't supported by LocalStack's free tier, so they're **plan-validated, never run locally** — and never claimed as "deployed."
- Two deliberate boundaries (documented, not gaps):
  1. **The managed Postgres+pgvector DB is an input, not provisioned here** — operators usually manage stateful databases separately. Provide its DSN via the `database_url` Secrets Manager secret (`secrets.tf`); the API resolves it.
  2. **AOSS is provisioned but the app's `VectorStore` doesn't bind to it yet** — an `OpenSearchStore` backend is the next step; AOSS is included as the validated cloud-native option. The app's live vector backend is pgvector.

## Usage

```bash
cd fossilrag/infra
terraform init
terraform validate                      # $0, no creds — what CI runs
cp terraform.tfvars.example terraform.tfvars   # edit name_prefix/region/...
terraform plan                           # needs AWS creds
terraform apply                          # GOES LIVE — incurs cost
# then populate the DB secret:
aws secretsmanager put-secret-value --secret-id "$(terraform output -raw database_url_secret_arn)" \
  --secret-string 'postgresql://user:pass@host:5432/fossilrag'
```

**LocalStack (dev, the supported services only):** install `terraform-local`
and run `tflocal apply` (it auto-generates the endpoint override). S3, SQS,
DynamoDB, Lambda, IAM, API Gateway, Secrets Manager work on the free Hobby tier
(needs `LOCALSTACK_AUTH_TOKEN`); Bedrock + AOSS do not.
