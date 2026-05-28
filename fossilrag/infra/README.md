# Infrastructure as Code (Terraform) — arrives in PR11

This directory will hold the **Terraform** stack that provisions FossilRAG on
AWS. It is intentionally **not** in the walking-skeleton PR (PR0): shipping
half-baked templates next to a skeleton would be misleading, so IaC lands as
its own focused, reviewable PR once the components it provisions exist.

## What PR11 will provision (live-deploy-ready, `$0` to author)

- **S3** raw + silver/gold buckets with lifecycle policies and cost tags
- **Lambda** functions (ingestion, chunking, embedding, enrichment) with
  reserved/provisioned concurrency and **application auto-scaling**
- **SQS** dead-letter queues + async failure destinations
  (`aws_lambda_function_event_invoke_config`) — the Auto-Scaling-Lambda+DLQ
  mutation
- **DynamoDB** (PAY_PER_REQUEST + TTL) for the idempotency ledger and the
  Prompt Fossilization cache
- **API Gateway** in front of the FastAPI retrieval service
- **OpenSearch Serverless** `VECTORSEARCH` collection (the cloud-native vector
  backend; pgvector remains the local/CI backend)
- **IAM** least-privilege roles; provider-level `default_tags` for cost
  allocation

## Honesty / cost posture

- Pinned to Terraform `>= 1.9` + AWS provider `~> 6.0` (current patterns; see
  [`../docs/adr/0001-foundational-decisions.md`](../docs/adr/0001-foundational-decisions.md)).
- Authored and **`terraform validate` / `plan`-verified at $0**. Bedrock and
  OpenSearch Serverless aren't emulable for free (LocalStack's free tier needs
  a token; AOSS isn't supported at all), so they are plan-validated, not
  run-locally — **never claimed as "deployed."**
- Going live is a single documented `terraform apply` you run with your own
  AWS credentials. For local AWS-service emulation in dev, the compose stack
  (PR12) uses LocalStack for the services it supports; tests use `moto`.
