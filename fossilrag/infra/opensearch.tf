# OpenSearch Serverless VECTORSEARCH collection — the cloud-native vector
# backend. Three policies are required (encryption = JSON OBJECT; network +
# data access = JSON ARRAYS — a classic jsonencode gotcha) and the collection
# can't be created without the encryption + network policies (depends_on).
#
# NOTE: this is provisioned + plan-validated, but the app's pgvector VectorStore
# does not yet bind to AOSS — an OpenSearchStore backend is the documented next
# step (docs/adr/0001). It is included as the validated cloud-native option.
resource "aws_opensearchserverless_security_policy" "encryption" {
  name = "${local.prefix}-enc"
  type = "encryption"
  policy = jsonencode({
    Rules       = [{ Resource = ["collection/${local.aoss_collection}"], ResourceType = "collection" }]
    AWSOwnedKey = true
  })
}

resource "aws_opensearchserverless_security_policy" "network" {
  name = "${local.prefix}-net"
  type = "network"
  policy = jsonencode([{
    Description = "public access to ${local.aoss_collection}"
    Rules = [
      { ResourceType = "collection", Resource = ["collection/${local.aoss_collection}"] },
      { ResourceType = "dashboard", Resource = ["collection/${local.aoss_collection}"] },
    ]
    AllowFromPublic = true
  }])
}

resource "aws_opensearchserverless_collection" "vectors" {
  name             = local.aoss_collection
  type             = "VECTORSEARCH"
  standby_replicas = "DISABLED" # cost-sensitive dev default

  depends_on = [
    aws_opensearchserverless_security_policy.encryption,
    aws_opensearchserverless_security_policy.network,
  ]
}

resource "aws_opensearchserverless_access_policy" "data" {
  name = "${local.prefix}-data"
  type = "data"
  policy = jsonencode([{
    Rules = [
      { ResourceType = "index", Resource = ["index/${local.aoss_collection}/*"], Permission = ["aoss:*"] },
      { ResourceType = "collection", Resource = ["collection/${local.aoss_collection}"], Permission = ["aoss:*"] },
    ]
    Principal = [aws_iam_role.api.arn]
  }])
}
