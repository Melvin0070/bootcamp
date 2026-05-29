output "raw_bucket" {
  description = "Raw upload bucket (S3 ObjectCreated → SQS ingest queue)."
  value       = aws_s3_bucket.this["raw"].bucket
}

output "silver_bucket" {
  value = aws_s3_bucket.this["silver"].bucket
}

output "gold_bucket" {
  value = aws_s3_bucket.this["gold"].bucket
}

output "ingest_queue_url" {
  value = aws_sqs_queue.ingest.url
}

output "ingest_dlq_url" {
  value = aws_sqs_queue.ingest_dlq.url
}

output "idempotency_table" {
  value = aws_dynamodb_table.idempotency.name
}

output "prompt_cache_table" {
  value = aws_dynamodb_table.prompt_cache.name
}

output "api_endpoint" {
  description = "Public HTTPS endpoint for the retrieval/mutate API."
  value       = aws_apigatewayv2_api.this.api_endpoint
}

output "aoss_collection_endpoint" {
  description = "OpenSearch Serverless collection data endpoint."
  value       = aws_opensearchserverless_collection.vectors.collection_endpoint
}

output "database_url_secret_arn" {
  description = "Populate this Secrets Manager secret with the Postgres+pgvector DSN."
  value       = aws_secretsmanager_secret.database_url.arn
}
