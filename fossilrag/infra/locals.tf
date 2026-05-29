data "aws_caller_identity" "current" {}

locals {
  prefix = "${var.name_prefix}-${var.environment}"

  raw_bucket    = "${local.prefix}-raw"
  silver_bucket = "${local.prefix}-silver"
  gold_bucket   = "${local.prefix}-gold"

  idempotency_table  = "${local.prefix}-idempotency"
  prompt_cache_table = "${local.prefix}-prompt-cache"

  ingest_queue = "${local.prefix}-ingest"
  ingest_dlq   = "${local.prefix}-ingest-dlq"

  aoss_collection = local.prefix # <= 32 chars; keep name_prefix short

  # The deployment package: zips the src tree for the source-hash. A real
  # deploy bundles dependencies (pypdf/boto3/...) via a layer or container
  # image; this keeps the stack plan-clean without a separate build step.
  lambda_handlers = {
    ingest = "fossilrag.ingest.handler.handler"
    worker = "fossilrag.worker.sqs.sqs_handler"
    dlq    = "fossilrag.worker.dlq.dlq_handler"
  }
}

data "archive_file" "package" {
  type        = "zip"
  source_dir  = "${path.module}/../src"
  output_path = "${path.module}/build/fossilrag.zip"
}
