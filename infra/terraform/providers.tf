provider "aws" {
  # IAM is global; the region matters for the bucket, the SNS topic and nothing else.
  # Budgets and Cost Explorer are global services: the provider routes them itself.
  region = var.results_bucket_region

  # Credentials come from AWS_PROFILE. `make tf-*` sets it from AWS_PROFILE_ADMIN,
  # an MFA-backed assume-role profile, and refuses to run as root.

  default_tags {
    tags = var.tags
  }
}

provider "awscc" {
  # Spending limits are regional and must live where the device does. All three approved
  # QPUs are in eu-north-1 (ADR-0004). awscc has no default_tags: spending_limits.tf tags
  # its resources explicitly with the same map.
  region = var.results_bucket_region
}
