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
# amazon.nova-lite-v1:0                         budget model
# us.amazon.nova-pro-v1:0                       stronger Amazon model
# us.anthropic.claude-haiku-4-5-20251001-v1:0   balanced Anthropic model
# us.anthropic.claude-sonnet-4-6                premium model
variable "bedrock_model_planner" {
  description = "Bedrock LLM model ID used to analyze questions into retrieval subqueries."
  default     = "amazon.nova-lite-v1:0"
}

variable "bedrock_model_reranker" {
  description = "Bedrock LLM model ID used to rerank retrieved evidence chunks."
  default     = "amazon.nova-lite-v1:0"
}

variable "bedrock_model_answer" {
  description = "Bedrock LLM model ID used to synthesize the final cited answer."
  default     = "amazon.nova-lite-v1:0"
}

variable "enable_guardduty" {
  description = "Enable GuardDuty. Free 30-day trial, then ~$1-4/mo. Recommended for prod."
  default     = false
}
