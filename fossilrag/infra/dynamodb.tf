# Self-Healing Idempotency ledger + Prompt Fossilization cache. Both PAY_PER_REQUEST
# (no capacity planning) with a TTL attribute (schema matches the app's
# DynamoDBStore / DynamoDBPromptCache: PK "key", TTL "ttl").
resource "aws_dynamodb_table" "idempotency" {
  name         = local.idempotency_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "key"

  attribute {
    name = "key"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_dynamodb_table" "prompt_cache" {
  name         = local.prompt_cache_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "key"

  attribute {
    name = "key"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}
