terraform {
  # strcontains() and lifecycle preconditions need 1.5+.
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    # Braket Spending Limits have no resource in the classic provider. awscc wraps the
    # CloudFormation type AWS::Braket::SpendingLimit; it landed in 1.79.0.
    awscc = {
      source  = "hashicorp/awscc"
      version = "~> 1.79"
    }
  }

  # No backend block on purpose: state is local (ADR-0002).
}
