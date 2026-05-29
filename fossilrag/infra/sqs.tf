# Ingestion queue (raw S3 events fan in here) + its DLQ. maxReceiveCount=5 →
# poison messages land in the DLQ; visibility >= 6x the worker Lambda timeout.
resource "aws_sqs_queue" "ingest_dlq" {
  name                      = local.ingest_dlq
  message_retention_seconds = 1209600 # 14 days
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "ingest" {
  name                       = local.ingest_queue
  visibility_timeout_seconds = 180
  message_retention_seconds  = 345600 # 4 days
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = 5
  })
}

# Allow the raw bucket to publish ObjectCreated events to the queue.
data "aws_iam_policy_document" "ingest_queue" {
  statement {
    effect  = "Allow"
    actions = ["sqs:SendMessage"]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
    resources = [aws_sqs_queue.ingest.arn]
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_s3_bucket.this["raw"].arn]
    }
  }
}

resource "aws_sqs_queue_policy" "ingest" {
  queue_url = aws_sqs_queue.ingest.id
  policy    = data.aws_iam_policy_document.ingest_queue.json
}

# raw → SQS (decoupled, burst-absorbing ingestion). One notification per bucket.
resource "aws_s3_bucket_notification" "raw" {
  bucket = aws_s3_bucket.this["raw"].id

  queue {
    queue_arn = aws_sqs_queue.ingest.arn
    events    = ["s3:ObjectCreated:*"]
  }

  depends_on = [aws_sqs_queue_policy.ingest]
}
