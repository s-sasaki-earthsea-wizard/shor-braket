# ---- Safety ----

variable "aws_account_id" {
  description = "Expected account ID. When set, applying under any other account fails before creating anything."
  type        = string
  default     = null
}

# ---- Region / S3 (bucket itself is created in Phase 3) ----

variable "results_bucket_region" {
  description = "Region for the results bucket and the provider. Keep it on the primary QPU region."
  type        = string
  default     = "eu-north-1"
}

variable "results_bucket_name" {
  description = "Results bucket name referenced by the IAM policies. Prefer the amazon-braket- prefix."
  type        = string
}

variable "simulator_region" {
  description = "Region for SV1/DM1. They are absent from eu-north-1, so simulator tasks and their result bucket live elsewhere."
  type        = string
  default     = "eu-west-2"
}

variable "simulator_bucket_name" {
  description = "Results bucket for simulator tasks, created in simulator_region. Braket writes results to a bucket in the region the task was submitted to."
  type        = string
  default     = null
}

variable "results_transition_days" {
  description = "Days before raw results move to Glacier. Used in Phase 3."
  type        = number
  default     = 90
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

# ---- Budget / alerts (used in Phase 3) ----

variable "monthly_budget_usd" {
  description = "Monthly budget in USD for AWS Budgets."
  type        = number
  default     = 100
}

variable "budget_notification_email" {
  description = "Email that receives budget alerts via SNS."
  type        = string
  sensitive   = true
  default     = null
}

variable "budget_alert_thresholds" {
  description = "Actual-spend thresholds (percent) that trigger an alert."
  type        = list(number)
  default     = [50, 80, 100]
}

# ---- Tags ----

variable "tags" {
  description = "Tags applied to every resource through provider default_tags."
  type        = map(string)
  default = {
    Project      = "shor-braket"
    ManagedBy    = "terraform"
    AmazonBraket = "true"
  }
}
