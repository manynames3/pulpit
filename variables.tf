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

# LLM model selection — swap to upgrade quality vs cost
# amazon.nova-lite-v1:0           ~$0.06/1M input  (default, budget)
# amazon.nova-pro-v1:0            ~$0.80/1M input  (mid tier)
# anthropic.claude-haiku-4-5-20251001   ~$0.80/1M input  (high quality)
# anthropic.claude-sonnet-4-6     ~$3.00/1M input  (best quality)
variable "bedrock_model_id" {
  description = "Bedrock LLM model ID. Swap to upgrade quality vs cost."
  default     = "amazon.nova-lite-v1:0"
}

variable "enable_guardduty" {
  description = "Enable GuardDuty. Free 30-day trial, then ~$1-4/mo. Recommended for prod."
  default     = false
}
