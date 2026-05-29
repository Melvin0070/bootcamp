variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment (dev|staging|prod) — used in names + tags."
  type        = string
  default     = "dev"
}

variable "name_prefix" {
  description = "Prefix for all resource names."
  type        = string
  default     = "fossilrag"
}

variable "lambda_runtime" {
  description = "Python runtime for the Lambda functions."
  type        = string
  default     = "python3.12"
}

variable "lambda_architecture" {
  description = "Lambda CPU architecture (arm64 is cheaper)."
  type        = string
  default     = "arm64"
}

variable "embed_dim" {
  description = "Embedding dimension for the OpenSearch Serverless knn_vector field (Titan v2)."
  type        = number
  default     = 1024
}

variable "api_provisioned_concurrency" {
  description = "Baseline provisioned concurrency for the latency-sensitive API Lambda."
  type        = number
  default     = 2
}

variable "api_max_concurrency" {
  description = "Max provisioned concurrency the API Lambda autoscales to."
  type        = number
  default     = 20
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the Lambda log groups."
  type        = number
  default     = 14
}
