# Monthly budget with SNS alerts (docs/03 §8). This is the soft stop: it notifies and
# never blocks. It also sees what the spending limits do not (S3, SNS, anything else the
# project tags), which is why it filters on the project tag rather than on the Braket service.
#
# The filter only matches once `project` is an active cost allocation tag (stage 2, #4).
# Before that the budget exists but counts nothing; the path check in between is accepted
# as untracked (0.3145 USD).

resource "aws_sns_topic" "budget_alerts" {
  name = "shor-braket-budget-alerts"
}

data "aws_iam_policy_document" "budget_alerts" {
  statement {
    sid    = "AllowBudgetsToPublish"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }

    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.budget_alerts.arn]

    # Confused-deputy guard: only budgets in this account may publish here.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "budget_alerts" {
  arn    = aws_sns_topic.budget_alerts.arn
  policy = data.aws_iam_policy_document.budget_alerts.json
}

resource "aws_sns_topic_subscription" "budget_email" {
  count = var.budget_notification_email == null ? 0 : 1

  topic_arn = aws_sns_topic.budget_alerts.arn
  protocol  = "email"
  endpoint  = var.budget_notification_email

  # SNS mails a confirmation link. Until it is confirmed the subscription is pending and no
  # alert is delivered. Terraform cannot confirm it (issue #4).
  #
  # Do NOT confirm it by clicking the link. Mail security scanners open links in incoming
  # mail, and the unauthenticated unsubscribe link then deletes the subscription; SNS
  # answers with a "deactivated" mail carrying a fresh resubscribe link, and the loop runs
  # by itself (measured 2026-09-17). Confirm with the API instead, which makes unsubscribing
  # require AWS credentials:
  #   aws sns confirm-subscription --topic-arn <topic> --token <Token from the mail link> \
  #     --authenticate-on-unsubscribe true
  # There is no Terraform argument for this; repeat it whenever this resource is recreated.
  # See ../terraform/README.md and the AWS Knowledge Center article
  # "prevent-unsubscribe-all-sns-topic".
}

resource "aws_budgets_budget" "monthly" {
  name         = "shor-braket-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = [format("user:project$%s", var.tags["project"])]
  }

  dynamic "notification" {
    for_each = var.budget_alert_thresholds

    content {
      comparison_operator       = "GREATER_THAN"
      threshold                 = notification.value
      threshold_type            = "PERCENTAGE"
      notification_type         = "ACTUAL"
      subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
    }
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "FORECASTED"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
  }

  # Budgets checks at creation that it may publish to the topic.
  depends_on = [aws_sns_topic_policy.budget_alerts]

  lifecycle {
    precondition {
      condition     = local.account_guard_ok
      error_message = local.account_guard_msg
    }
  }
}
