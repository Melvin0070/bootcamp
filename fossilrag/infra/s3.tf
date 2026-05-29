# Medallion buckets. AWS provider v6 decomposes the old monolithic
# aws_s3_bucket: versioning, encryption, lifecycle, public-access each in their
# own resource. raw is transient (expires); silver/gold transition to IA.
locals {
  buckets = {
    raw    = local.raw_bucket
    silver = local.silver_bucket
    gold   = local.gold_bucket
  }
}

resource "aws_s3_bucket" "this" {
  for_each = local.buckets
  bucket   = each.value
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = aws_s3_bucket.this
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy      = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id

  rule {
    id     = "transition-and-cleanup"
    status = "Enabled"
    filter {} # whole bucket

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    # raw is a landing zone — expire it; silver/gold are retained.
    dynamic "expiration" {
      for_each = each.key == "raw" ? [1] : []
      content {
        days = 90
      }
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}
