# Cost allocation tags: what lets the budget filter on `project` and the bill be split by
# `oracle` and `campaign`. Activation is the only thing Terraform does here; the keys
# themselves come from the tagged resources (default_tags) and from the quantum tasks.
#
# A key cannot be activated before it has appeared in the billing records, which takes
# about 24 hours after the first tagged resource or task. Hence the variable: empty on the
# first apply, then grown in two later applies (issue #4). Destroying one sets it back to
# Inactive; the key itself cannot be removed.

resource "aws_ce_cost_allocation_tag" "keys" {
  for_each = toset(var.active_cost_allocation_tags)

  tag_key = each.key
  status  = "Active"
}
