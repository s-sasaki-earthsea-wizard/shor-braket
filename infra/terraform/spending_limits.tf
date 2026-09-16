# Braket Spending Limits: the hard stop for QPU spend (docs/03 §7).
#
# The service compares every CreateQuantumTask against limit - totalSpend - queuedSpend on
# that device and refuses the task when the estimate does not fit. Unlike the client gate,
# nothing on the operator's side can talk its way past this: the execution and AQT roles may
# only read the limits (guardrail policy). Changing one is a Terraform apply under the admin
# role, that is, an MFA-backed, CloudTrail-visible act.
#
# Every device starts at 0 USD. Raising a limit for an experiment is a tfvars change.

locals {
  # The ceiling for the three limits together. A constant on purpose: raising it is a
  # design change (docs/03 §7), not a tfvars edit. variables.tf repeats the number in the
  # validation of var.spending_limits, because Terraform 1.5 validations cannot read locals.
  qpu_spending_ceiling_usd = 300

  # Logical name -> device ARN. Same keys as src/shor_braket/cost.py, same three devices
  # the IAM guardrail leaves reachable. Device ARNs carry no account id.
  approved_qpus = {
    garnet  = "arn:aws:braket:eu-north-1::device/qpu/iqm/Garnet"
    emerald = "arn:aws:braket:eu-north-1::device/qpu/iqm/Emerald"
    ibex    = "arn:aws:braket:eu-north-1::device/qpu/aqt/Ibex-Q1"
  }

  spending_limit_total_usd = sum([for l in values(var.spending_limits) : l.limit_usd])

  # awscc has no default_tags and wants a set of {key, value} objects.
  awscc_tags = [for k, v in var.tags : { key = k, value = v }]
}

resource "awscc_braket_spending_limit" "qpu" {
  for_each = local.approved_qpus

  device_arn = each.value

  # The API stores the amount as a string with at most two decimals.
  spending_limit = format("%.2f", var.spending_limits[each.key].limit_usd)

  # Optional period; both ends or neither (validated in variables.tf). Without one the
  # limit is always in force. A 0 USD limit blocks with or without a period.
  time_period = var.spending_limits[each.key].start_at == null ? null : {
    start_at = var.spending_limits[each.key].start_at
    end_at   = var.spending_limits[each.key].end_at
  }

  tags = local.awscc_tags

  lifecycle {
    # A deleted limit is no limit at all. To tear the project down, remove this line first.
    prevent_destroy = true

    precondition {
      condition     = local.spending_limit_total_usd <= local.qpu_spending_ceiling_usd
      error_message = "The three spending limits add up to ${local.spending_limit_total_usd} USD, above the ${local.qpu_spending_ceiling_usd} USD ceiling (docs/03 §7)."
    }

    precondition {
      condition     = strcontains(each.value, ":${var.results_bucket_region}:")
      error_message = "Spending limits are regional. ${each.key} is not in ${var.results_bucket_region}, where the awscc provider is configured."
    }

    precondition {
      condition     = local.account_guard_ok
      error_message = local.account_guard_msg
    }
  }
}
