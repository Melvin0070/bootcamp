# Activity 1: Fix a Broken Serverless Data Pipeline

**Week:** 1 | **Day:** 1 | **Course alignment:** Agentic AI

---

## Problem Statement

A serverless document ingestion pipeline has three critical flaws:

| Problem | Symptom |
|---------|---------|
| **Lambda timeout (3s)** | Large documents silently fail; message retries forever |
| **No concurrency limit** | Burst traffic spins up 1000s of concurrent Lambdas, hammering S3 |
| **No DLQ / no retries** | Broken messages retry until the 4-day retention expires; no alert fires |
| **BatchSize=1** | One Lambda invocation per message — 10× more expensive than needed |
| **No partial batch response** | One bad record causes the entire batch to be retried |
| **Hardcoded bucket names** | Breaks in any environment other than the original dev account |

---

## What Was Fixed

- [x] **Timeout** raised to 60s; `VisibilityTimeout` set to 360s (6× rule)
- [x] **Reserved concurrency** capped at 10 (configurable via parameter)
- [x] **DLQ** added with `maxReceiveCount: 3`
- [x] **Partial batch response** (`ReportBatchItemFailures`) — only failed records retry
- [x] **BatchSize=10** with 30s batch window — ~90% fewer Lambda invocations
- [x] **Environment variables** replace all hardcoded bucket names
- [x] **Structured JSON logging** — every record logs event, key, bytes, errors
- [x] **Idempotency** — DynamoDB table tracks processed documents; re-runs skip them
- [x] **CloudWatch Alarm** — fires when first message lands in DLQ
- [x] **Cost tags** on all resources

---

## Repo Layout

```
activity-01-lambda-pipeline/
├── broken/                  ← original broken code (do not edit)
│   ├── lambda_function.py
│   ├── template.yaml
│   └── requirements.txt
├── lambda_function.py       ← fixed Lambda handler
├── template.yaml            ← fixed SAM/CloudFormation template
├── requirements.txt         ← Lambda runtime dependencies
├── requirements-dev.txt     ← test dependencies
├── .env.example             ← required environment variables
├── tests/
│   └── test_lambda.py       ← unit tests (pytest)
└── docs/
    └── architecture.md      ← diagrams + trade-off table
```

---

## Running Tests Locally

```bash
# Install test dependencies
pip install -r requirements-dev.txt

# Run tests with coverage report
pytest tests/ -v --cov=lambda_function --cov-report=term-missing
```

---

## Deploying to AWS

Prerequisites: AWS CLI configured, SAM CLI installed.

```bash
# 1. Create the S3 buckets (one-time setup)
aws s3 mb s3://fossilrag-raw-dev
aws s3 mb s3://fossilrag-silver-dev

# 2. Build and deploy the SAM stack
sam build
sam deploy \
  --stack-name fossilrag-ingestion-dev \
  --parameter-overrides Environment=dev \
  --capabilities CAPABILITY_IAM \
  --resolve-s3

# 3. Verify the stack
aws cloudformation describe-stacks \
  --stack-name fossilrag-ingestion-dev \
  --query 'Stacks[0].StackStatus'
```

---

## Testing End-to-End on AWS

```bash
# Upload a test document to the raw bucket
echo "Dino fossil report: T-Rex found in Montana, age 68Ma." \
  > /tmp/test-doc.txt
aws s3 cp /tmp/test-doc.txt s3://fossilrag-raw-dev/docs/test-doc.txt

# Get the SQS queue URL from stack outputs
QUEUE_URL=$(aws cloudformation describe-stacks \
  --stack-name fossilrag-ingestion-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`DocumentQueueUrl`].OutputValue' \
  --output text)

# Send a processing request
aws sqs send-message \
  --queue-url "$QUEUE_URL" \
  --message-body '{"s3_key": "docs/test-doc.txt"}'

# Check CloudWatch Logs for the result
aws logs tail /aws/lambda/fossilrag-document-processor-dev --follow
```

---

## PR Rubric Self-Check

| Criterion | Evidence |
|-----------|----------|
| **Code Correctness** — all cases, edge cases, extra validation | Partial batch response; idempotency; error isolation per record; all 20 unit tests pass |
| **Problem Solving & Architecture** — scalable, best practices, trade-offs | VisibilityTimeout = 6× rule; DLQ with maxReceiveCount=3; reserved concurrency; batch window; trade-off table in `docs/architecture.md` |
| **Code Quality** — production-ready, env vars, commented | No hardcoded values; structured JSON logging; modular functions; `.env.example` provided |
| **PR Description** — comprehensive, diagrams, edge cases, video | Architecture diagrams in `docs/architecture.md`; ASCII before/after; edge cases listed |
| **Completeness** — all changes + tests + docs | 20 tests; `docs/architecture.md`; deploy instructions above; CloudWatch alarm |
