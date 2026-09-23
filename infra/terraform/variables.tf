# ---- Safety ----

variable "aws_account_id" {
  description = "Expected account ID. When set, applying under any other account fails before creating anything."
  type        = string
  default     = null
}

# ---- Region / S3 ----

variable "results_bucket_region" {
  description = "Region for the results bucket, the SNS topic, the spending limits and both providers. Braket writes results to a bucket in the region the task was submitted to, and all three approved QPUs are in eu-north-1, so this is the only region the project uses (ADR-0004)."
  type        = string
  default     = "eu-north-1"
}

variable "results_bucket_name" {
  description = "Results bucket. Must start with amazon-braket-: that prefix is what the Braket service-linked role is allowed to write to, so no bucket policy is needed. The same name goes into .env as BRAKET_RESULTS_BUCKET and is rendered into the IAM policies."
  type        = string

  validation {
    condition     = startswith(var.results_bucket_name, "amazon-braket-")
    error_message = "results_bucket_name must start with amazon-braket-."
  }
}

# ---- IAM principals ----

variable "operator_user_name" {
  description = "Operator user: read-only plus assume into the execution role. Registers an MFA device."
  type        = string
  default     = "shor-braket-operator"
}

variable "monitor_user_name" {
  description = "Monitor user: read-only, no assume. Cannot execute regardless of MFA."
  type        = string
  default     = "shor-braket-monitor"
}

variable "execution_role_name" {
  description = "Role for everyday task creation on IQM. Assumed by the operator with MFA. Carries an unconditional deny on AQT."
  type        = string
  default     = "ShorBraketExecutionRole"
}

variable "aqt_role_name" {
  description = "Role that may create tasks on AQT and nothing else. Assumed by the operator with MFA. Keeping it separate is what makes reaching the 13.6x device a deliberate, CloudTrail-visible act (ADR-0004)."
  type        = string
  default     = "ShorBraketAqtRole"
}

# ---- Braket Spending Limits (the QPU hard stop, docs/03 §7) ----

variable "spending_limits" {
  description = "Braket Spending Limit per approved QPU, in USD, keyed by the project's logical device names (the same keys as src/shor_braket/cost.py). 0 makes the service refuse every task on that device, whatever the client says. The three values together may not exceed 300 USD; that ceiling is deliberately not a variable. A period is optional: when set, both ends are required (ISO 8601) and tasks outside it are refused."
  type = map(object({
    limit_usd = number
    start_at  = optional(string)
    end_at    = optional(string)
  }))
  default = {
    garnet  = { limit_usd = 0 }
    emerald = { limit_usd = 0 }
    ibex    = { limit_usd = 0 }
  }

  validation {
    condition     = toset(keys(var.spending_limits)) == toset(["garnet", "emerald", "ibex"])
    error_message = "spending_limits must have exactly the keys garnet, emerald and ibex: the three QPUs the IAM guardrail leaves reachable."
  }

  validation {
    condition     = alltrue([for l in values(var.spending_limits) : l.limit_usd >= 0 && l.limit_usd * 100 == floor(l.limit_usd * 100)])
    error_message = "Each limit_usd must be non-negative with at most two decimals; the API stores the amount as a string matching \\d+(\\.\\d{1,2})?."
  }

  validation {
    # The same 300 appears as local.qpu_spending_ceiling_usd in spending_limits.tf, which
    # the resource precondition checks. Terraform 1.5 cannot reference a local from here.
    condition     = sum([for l in values(var.spending_limits) : l.limit_usd]) <= 300
    error_message = "The three spending limits add up to more than 300 USD. The ceiling is the sum over all devices, not per device (docs/03 §7), and it is not a variable."
  }

  validation {
    condition     = alltrue([for l in values(var.spending_limits) : (l.start_at == null) == (l.end_at == null)])
    error_message = "start_at and end_at come together: the API requires both ends of a period. Omit both for a limit that is always in force."
  }
}

# ---- Cost allocation tags (two-stage apply, issue #4) ----

variable "allow_teardown" {
  description = "Set true only to tear the project down, after the results have been copied out. It lets destroy empty the results bucket and delete the IAM users together with the access keys and MFA devices that were issued outside Terraform. Apply once with it true, then destroy. The spending limits additionally carry prevent_destroy, which has to be removed from spending_limits.tf by hand because lifecycle arguments cannot be variables."
  type        = bool
  default     = false
}

variable "active_cost_allocation_tags" {
  description = "Tag keys to activate for cost allocation. Leave empty on the first apply: a key can only be activated about 24 hours after a resource carrying it first appears in the billing records. Stage 2 (a day after this infrastructure exists) adds project; stage 3 (a day after the first quantum task) adds oracle and campaign."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for k in var.active_cost_allocation_tags : contains(["project", "oracle", "campaign"], k)])
    error_message = "Only project, oracle and campaign are activated: the keys every quantum task carries (gate/tags.py)."
  }
}

# ---- Budget / alerts ----

variable "monthly_budget_usd" {
  description = "Monthly budget in USD for AWS Budgets. A soft stop: it notifies, it does not block. The hard stop for QPU spend is the spending limits."
  type        = number
  default     = 100
}

variable "budget_notification_email" {
  description = "Email subscribed to the budget alert topic. SNS sends a confirmation mail that has to be clicked once; Terraform cannot do that (issue #4). null skips the subscription."
  type        = string
  sensitive   = true
  default     = null
}

variable "budget_alert_thresholds" {
  description = "Actual-spend thresholds (percent of the monthly budget) that trigger an alert. A forecast alert at 100% is added on top."
  type        = list(number)
  default     = [50, 80, 100]
}

# ---- Tags ----

variable "tags" {
  description = "Tags applied to every resource through provider default_tags (and explicitly on the awscc resources). `project` is lowercase on purpose: it is the same key every quantum task carries (gate/tags.py), and cost allocation tag keys are case-sensitive, so one key covers the infrastructure and the tasks. The budget filters on it."
  type        = map(string)
  default = {
    project      = "shor-braket"
    "managed-by" = "terraform"
  }

  validation {
    condition     = contains(keys(var.tags), "project")
    error_message = "tags must contain the project key: the budget's cost filter and the cost allocation tag are built from it."
  }
}
