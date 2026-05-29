# Pinned per docs/adr/0001 (verified May 2026): Terraform core >= 1.9, AWS
# provider v6 line (6.47 current). The archive provider builds the Lambda zip.
terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}
