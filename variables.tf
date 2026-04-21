variable "aws_region" {
  description = "AWS region for all resources."
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment: dev or prod."
  default     = "dev"
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "Environment must be dev or prod."
  }
}

variable "church_name" {
  description = "Full church name — used in guardrails response messaging."
  default     = "Atlanta Bethel Church"
}

variable "pastor_contact" {
  description = "Contact info returned when a crisis or pastoral query is detected."
  default     = "Please contact our pastoral team directly for support."
}

# YouTube ingestion
variable "youtube_channel_id" {
  description = "YouTube channel ID to ingest sermons from."
}

variable "youtube_api_key" {
  description = "YouTube Data API v3 key. Store in SSM or pass via env."
  sensitive   = true
}

variable "ingest_schedule" {
  description = "EventBridge cron for ingestion. Default: every Monday 6am UTC."
  default     = "cron(0 6 ? * MON *)"
}

# LLM model selection
# Options (cheapest to most capable):
#   amazon.nova-lite-v1:0          — default, ~$0.06/1M input tokens
#   amazon.nova-pro-v1:0           — mid tier, ~$0.80/1M input tokens
#   anthropic.claude-haiku-4-5-20251001  — high quality, ~$0.80/1M input tokens
#   anthropic.claude-sonnet-4-6    — best quality, ~$3.00/1M input tokens
variable "bedrock_model_id" {
  description = "Bedrock LLM model ID. Swap to upgrade quality vs cost."
  default     = "amazon.nova-lite-v1:0"
}

# Security toggles
variable "enable_guardduty" {
  description = "Enable GuardDuty threat detection. Free 30-day trial, then ~$1-4/mo. Recommended for prod."
  default     = false
}
