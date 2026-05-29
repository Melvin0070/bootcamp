# Provider-level default_tags attach cost-allocation tags to every managed
# resource (Cost Explorer groups by these). LocalStack dev overrides live in
# localstack.tf.example (or use the `tflocal` wrapper).
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "FossilRAG"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
