# All functions share one deployment package (different handlers). Production
# bundles dependencies via a layer or container image; see docs/runbook.md.

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/aws/lambda/${local.prefix}-worker"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "dlq" {
  name              = "/aws/lambda/${local.prefix}-dlq"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.prefix}-api"
  retention_in_days = var.log_retention_days
}

# ---- SQS ingestion worker (partial-batch-failure; idempotent) --------------
resource "aws_lambda_function" "worker" {
  function_name    = "${local.prefix}-worker"
  role             = aws_iam_role.worker.arn
  runtime          = var.lambda_runtime
  handler          = local.lambda_handlers.worker
  filename         = data.archive_file.package.output_path
  source_code_hash = data.archive_file.package.output_base64sha256
  memory_size      = 512
  timeout          = 30
  architectures    = [var.lambda_architecture]

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      FOSSILRAG_SILVER_BUCKET       = local.silver_bucket
      FOSSILRAG_IDEMPOTENCY_BACKEND = "dynamodb"
      FOSSILRAG_IDEMPOTENCY_TABLE   = local.idempotency_table
      FOSSILRAG_LOG_LEVEL           = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.worker]
}

resource "aws_lambda_event_source_mapping" "worker" {
  event_source_arn                   = aws_sqs_queue.ingest.arn
  function_name                      = aws_lambda_function.worker.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 5
  # Partial-batch responses: only the messages the worker reports are retried.
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 20 # cap fan-out so we don't exhaust downstream limits
  }
}

# ---- DLQ handler -----------------------------------------------------------
resource "aws_lambda_function" "dlq" {
  function_name    = "${local.prefix}-dlq"
  role             = aws_iam_role.worker.arn
  runtime          = var.lambda_runtime
  handler          = local.lambda_handlers.dlq
  filename         = data.archive_file.package.output_path
  source_code_hash = data.archive_file.package.output_base64sha256
  memory_size      = 256
  timeout          = 30
  architectures    = [var.lambda_architecture]

  environment {
    variables = {
      FOSSILRAG_LOG_LEVEL = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.dlq]
}

resource "aws_lambda_event_source_mapping" "dlq" {
  event_source_arn = aws_sqs_queue.ingest_dlq.arn
  function_name    = aws_lambda_function.dlq.arn
  batch_size       = 10
}

# ---- Retrieval / mutate API (Mangum ASGI adapter) --------------------------
resource "aws_lambda_function" "api" {
  function_name    = "${local.prefix}-api"
  role             = aws_iam_role.api.arn
  runtime          = var.lambda_runtime
  handler          = "fossilrag.api.asgi.handler" # Mangum(app)
  filename         = data.archive_file.package.output_path
  source_code_hash = data.archive_file.package.output_base64sha256
  memory_size      = 1024
  timeout          = 30
  architectures    = [var.lambda_architecture]
  publish          = true # required for the alias + provisioned concurrency

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      FOSSILRAG_EMBED_PROVIDER          = "bedrock"
      FOSSILRAG_EMBED_MODEL             = "amazon.titan-embed-text-v2:0"
      FOSSILRAG_EMBED_DIM               = tostring(var.embed_dim)
      FOSSILRAG_LLM_PROVIDER            = "bedrock"
      FOSSILRAG_PROMPT_CACHE_BACKEND    = "dynamodb"
      FOSSILRAG_PROMPT_CACHE_TABLE      = local.prompt_cache_table
      FOSSILRAG_DATABASE_URL_SECRET_ARN = aws_secretsmanager_secret.database_url.arn
      FOSSILRAG_LOG_LEVEL               = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.api]
}

resource "aws_lambda_alias" "api_live" {
  name             = "live"
  function_name    = aws_lambda_function.api.function_name
  function_version = aws_lambda_function.api.version
}

# Warm baseline so cold starts don't hit the retrieval path...
resource "aws_lambda_provisioned_concurrency_config" "api" {
  function_name                     = aws_lambda_function.api.function_name
  qualifier                         = aws_lambda_alias.api_live.name
  provisioned_concurrent_executions = var.api_provisioned_concurrency
}

# ...and autoscale it under load (target-tracking on utilisation).
resource "aws_appautoscaling_target" "api" {
  service_namespace  = "lambda"
  scalable_dimension = "lambda:function:ProvisionedConcurrency"
  resource_id        = "function:${aws_lambda_function.api.function_name}:${aws_lambda_alias.api_live.name}"
  min_capacity       = var.api_provisioned_concurrency
  max_capacity       = var.api_max_concurrency
}

resource "aws_appautoscaling_policy" "api" {
  name               = "${local.prefix}-api-pc"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.api.service_namespace
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  resource_id        = aws_appautoscaling_target.api.resource_id

  target_tracking_scaling_policy_configuration {
    target_value = 0.7
    predefined_metric_specification {
      predefined_metric_type = "LambdaProvisionedConcurrencyUtilization"
    }
  }
}
