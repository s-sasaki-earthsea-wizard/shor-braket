# Results bucket. One bucket, in the region of the three approved QPUs (ADR-0004).
#
# No versioning and no Glacier transition: a task result is well under a megabyte, so a
# hundred of them cost a fraction of a cent per month in Standard, and Glacier would add
# per-object metadata overhead and transition requests that cost more than they save.
# The results are copied to the NAS when the experiments are done, then this is destroyed.

resource "aws_s3_bucket" "results" {
  bucket = var.results_bucket_name

  # False unless var.allow_teardown: `make tf-destroy` refuses while results are still inside.
  # Copy them out first (the 2026-09-23 results went to the NAS), then flip the variable.
  force_destroy = var.allow_teardown

  lifecycle {
    precondition {
      condition     = local.account_guard_ok
      error_message = local.account_guard_msg
    }
  }
}

resource "aws_s3_bucket_public_access_block" "results" {
  bucket = aws_s3_bucket.results.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "results" {
  bucket = aws_s3_bucket.results.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "results" {
  bucket = aws_s3_bucket.results.id

  # The only rule is hygiene: an upload that never completed is not a result.
  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}
