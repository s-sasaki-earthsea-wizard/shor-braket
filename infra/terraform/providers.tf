provider "aws" {
  # IAM is global; the region only matters for the regional resources added in Phase 3.
  region = var.results_bucket_region

  # Credentials come from AWS_PROFILE. `make tf-*` sets it from AWS_PROFILE_ADMIN,
  # an MFA-backed assume-role profile, and refuses to run as root.

  default_tags {
    tags = var.tags
  }
}
