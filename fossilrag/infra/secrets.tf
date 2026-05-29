# The API's Postgres+pgvector DSN. The managed database (RDS/Aurora pgvector) is
# a stateful prerequisite operators usually manage separately, so it's provided
# as a secret rather than provisioned here. Populate after apply:
#   aws secretsmanager put-secret-value --secret-id <arn> \
#     --secret-string 'postgresql://user:pass@host:5432/fossilrag'
# (The app reads DATABASE_URL; wire the secret via the Lambda secrets extension
# or an entrypoint that exports it — see docs/runbook.md.)
resource "aws_secretsmanager_secret" "database_url" {
  name        = "${local.prefix}-database-url"
  description = "Postgres+pgvector DSN for the FossilRAG retrieval API."
}
