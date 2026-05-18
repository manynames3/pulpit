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

variable "youtube_channel_id" {
  description = "YouTube channel ID to ingest sermons from."
}

variable "ingest_schedule" {
  description = "EventBridge cron for ingestion. Default: every Monday 6am UTC."
  default     = "cron(0 6 ? * MON *)"
}

# LLM model selection. Titan Embed Text v2 remains the retrieval embedding model.
# amazon.nova-lite-v1:0                         budget answer model
# us.amazon.nova-pro-v1:0                       stronger Amazon answer model
# us.anthropic.claude-haiku-4-5-20251001-v1:0   balanced Anthropic answer model
# us.anthropic.claude-sonnet-4-6                premium answer model
variable "bedrock_model_id" {
  description = "Bedrock LLM model ID. Swap to upgrade quality vs cost."
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "enable_guardduty" {
  description = "Enable GuardDuty. Free 30-day trial, then ~$1-4/mo. Recommended for prod."
  default     = false
}
