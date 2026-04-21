# GuardDuty is off by default (enable_guardduty = false).
# Set enable_guardduty = true in prod terraform.tfvars.
# Cost: free 30-day trial, then ~$1-4/month depending on data volume.
# Provides: credential abuse detection, crypto mining alerts, recon detection.
# Not critical for serverless-only architecture but recommended for production.

resource "aws_guardduty_detector" "pulpit" {
  count  = var.enable_guardduty ? 1 : 0
  enable = true
  tags   = local.tags
}
