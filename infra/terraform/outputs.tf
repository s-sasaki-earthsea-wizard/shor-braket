output "monitor_user_name" {
  value = aws_iam_user.monitor.name
}

output "operator_user_name" {
  value = aws_iam_user.operator.name
}

output "execution_role_arn" {
  value     = aws_iam_role.execution.arn
  sensitive = true
}

output "aqt_role_arn" {
  value     = aws_iam_role.aqt.arn
  sensitive = true
}

output "operator_mfa_serial" {
  description = "Serial the operator's virtual MFA device will have once registered under the same name."
  value       = "arn:aws:iam::${local.account_id}:mfa/${aws_iam_user.operator.name}"
  sensitive   = true
}

output "aws_config_snippet" {
  description = "Paste into ~/.aws/config. Retrieve with: terraform output -raw aws_config_snippet"
  sensitive   = true
  value       = <<-EOT
    [profile shor-braket-ro]
    region = ${var.results_bucket_region}
    # long-term key of ${aws_iam_user.operator.name} goes in ~/.aws/credentials under [shor-braket-ro]

    [profile shor-braket-exec]
    source_profile   = shor-braket-ro
    role_arn         = ${aws_iam_role.execution.arn}
    mfa_serial       = arn:aws:iam::${local.account_id}:mfa/${aws_iam_user.operator.name}
    region           = ${var.results_bucket_region}
    duration_seconds = 3600

    # AQT only. A separate AssumeRole so that reaching the expensive device is a deliberate,
    # CloudTrail-visible act (ADR-0004). Use it for `make submit-qpu DEVICE=ibex` and nothing else.
    [profile shor-braket-aqt]
    source_profile   = shor-braket-ro
    role_arn         = ${aws_iam_role.aqt.arn}
    mfa_serial       = arn:aws:iam::${local.account_id}:mfa/${aws_iam_user.operator.name}
    region           = ${var.results_bucket_region}
    duration_seconds = 3600
  EOT
}
