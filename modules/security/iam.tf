# Account-level IAM baseline.
# Individual Lambda roles are defined in their respective modules.
# Principle: every role gets only what it needs — no wildcards, no Action: "*"

locals {
  tags = {
    Project     = "pulpit"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
