# IAM principals for shor-braket.
#
# The policy documents in ../iam/*.json are the single source of truth. This file
# only substitutes the placeholders (the same way `make iam-render` does with sed)
# and wires the attachments. Device deny lists and the AQT tag gate live in the
# JSON, not in variables. See ../iam/README.md.

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  iam_dir    = "${path.module}/../iam"

  policy_files = {
    readonly    = "shor-braket-readonly-policy.json"
    assume_exec = "shor-braket-assume-exec-policy.json"
    execute     = "shor-braket-execute-policy.json"
    guardrail   = "shor-braket-guardrail-policy.json"
  }

  rendered = {
    for key, name in local.policy_files :
    key => replace(
      replace(file("${local.iam_dir}/${name}"), "<AWS_ACCOUNT_ID>", local.account_id),
      "<RESULTS_BUCKET>", var.results_bucket_name
    )
  }

  trust_policy = replace(
    file("${local.iam_dir}/execution-role-trust-policy.json"),
    "<AWS_ACCOUNT_ID>", local.account_id
  )

  account_guard_ok  = var.aws_account_id == null || var.aws_account_id == local.account_id
  account_guard_msg = "Caller account does not match var.aws_account_id. Check AWS_PROFILE before applying."
}

# ---- Users ----

resource "aws_iam_user" "monitor" {
  name = var.monitor_user_name

  lifecycle {
    precondition {
      condition     = local.account_guard_ok
      error_message = local.account_guard_msg
    }
    precondition {
      condition     = strcontains(local.rendered.readonly, ":user/${var.monitor_user_name}\"")
      error_message = "shor-braket-readonly-policy.json does not reference user/${var.monitor_user_name}. Keep the JSON and var.monitor_user_name in sync."
    }
  }
}

resource "aws_iam_user" "operator" {
  name = var.operator_user_name

  lifecycle {
    precondition {
      condition     = local.account_guard_ok
      error_message = local.account_guard_msg
    }
    precondition {
      condition     = strcontains(local.trust_policy, ":user/${var.operator_user_name}\"")
      error_message = "execution-role-trust-policy.json does not name user/${var.operator_user_name} as principal. Keep the JSON and var.operator_user_name in sync."
    }
  }
}

# ---- Managed policies (rendered from ../iam) ----

resource "aws_iam_policy" "readonly" {
  name        = "shor-braket-readonly"
  description = "Read-only access to shor-braket resources. No task creation, no assume."
  policy      = local.rendered.readonly
}

resource "aws_iam_policy" "assume_exec" {
  name        = "shor-braket-assume-exec"
  description = "sts:AssumeRole into the execution role. MFA is enforced by the trust policy."
  policy      = local.rendered.assume_exec
}

resource "aws_iam_policy" "execute" {
  name        = "shor-braket-execute"
  description = "Create and manage Braket quantum tasks, write results, log usage."
  policy      = local.rendered.execute
}

resource "aws_iam_policy" "guardrail" {
  name        = "shor-braket-guardrail"
  description = "Deny-only guardrail: budget-breaking devices, MFA-less task creation, hybrid jobs, notebooks, AQT without campaign tag."
  policy      = local.rendered.guardrail
}

# ---- Execution role ----

resource "aws_iam_role" "execution" {
  name                 = var.execution_role_name
  description          = "Creates quantum tasks. Assumed by ${var.operator_user_name} with MFA; sessions expire after one hour."
  assume_role_policy   = local.trust_policy
  max_session_duration = 3600

  # IAM rejects a trust policy that names a principal which does not exist yet.
  depends_on = [aws_iam_user.operator]

  lifecycle {
    precondition {
      condition     = local.account_guard_ok
      error_message = local.account_guard_msg
    }
    precondition {
      condition     = strcontains(local.rendered.assume_exec, ":role/${var.execution_role_name}\"")
      error_message = "shor-braket-assume-exec-policy.json does not reference role/${var.execution_role_name}. Keep the JSON and var.execution_role_name in sync."
    }
  }
}

# ---- Attachments ----

resource "aws_iam_user_policy_attachment" "monitor_readonly" {
  user       = aws_iam_user.monitor.name
  policy_arn = aws_iam_policy.readonly.arn
}

resource "aws_iam_user_policy_attachment" "monitor_guardrail" {
  user       = aws_iam_user.monitor.name
  policy_arn = aws_iam_policy.guardrail.arn
}

resource "aws_iam_user_policy_attachment" "operator_readonly" {
  user       = aws_iam_user.operator.name
  policy_arn = aws_iam_policy.readonly.arn
}

resource "aws_iam_user_policy_attachment" "operator_assume_exec" {
  user       = aws_iam_user.operator.name
  policy_arn = aws_iam_policy.assume_exec.arn
}

resource "aws_iam_user_policy_attachment" "operator_guardrail" {
  user       = aws_iam_user.operator.name
  policy_arn = aws_iam_policy.guardrail.arn
}

resource "aws_iam_role_policy_attachment" "execution_execute" {
  role       = aws_iam_role.execution.name
  policy_arn = aws_iam_policy.execute.arn
}

resource "aws_iam_role_policy_attachment" "execution_guardrail" {
  role       = aws_iam_role.execution.name
  policy_arn = aws_iam_policy.guardrail.arn
}
