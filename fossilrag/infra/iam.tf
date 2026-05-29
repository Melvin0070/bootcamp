data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ----------------------------------------------------------------------------
# Worker role — ingestion SQS worker + DLQ handler.
# ----------------------------------------------------------------------------
resource "aws_iam_role" "worker" {
  name               = "${local.prefix}-worker"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "worker_basic" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "worker" {
  statement {
    sid       = "ReadRaw"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.this["raw"].arn}/*"]
  }
  statement {
    sid       = "WriteGoldSilver"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.this["silver"].arn}/*", "${aws_s3_bucket.this["gold"].arn}/*"]
  }
  statement {
    sid       = "ConsumeQueues"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.ingest.arn, aws_sqs_queue.ingest_dlq.arn]
  }
  statement {
    sid = "IdempotencyLedger"
    actions = [
      "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
      "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan",
    ]
    resources = [aws_dynamodb_table.idempotency.arn]
  }
}

resource "aws_iam_role_policy" "worker" {
  name   = "${local.prefix}-worker"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

# ----------------------------------------------------------------------------
# API role — retrieval/mutate API (Bedrock + prompt cache + AOSS + DB secret).
# ----------------------------------------------------------------------------
resource "aws_iam_role" "api" {
  name               = "${local.prefix}-api"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "api_basic" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "BedrockInvoke"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = ["*"] # cross-region inference profiles span regions; tighten to model ARNs in prod
  }
  statement {
    sid       = "PromptCache"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.prompt_cache.arn]
  }
  statement {
    sid       = "AossDataPlane"
    actions   = ["aoss:APIAccessAll"]
    resources = ["arn:aws:aoss:${var.aws_region}:${data.aws_caller_identity.current.account_id}:collection/*"]
  }
  statement {
    sid       = "DbSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.database_url.arn]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "${local.prefix}-api"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}
