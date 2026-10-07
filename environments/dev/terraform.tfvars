environment        = "dev"
aws_region         = "us-east-1"
church_name        = "Atlanta Bethel Church"
pastor_contact     = "Please contact our pastoral team at abc@atlbethel.org"
youtube_channel_id = "UCchY0Iagf_2cCP0RGVwQ-FA"
enable_guardduty   = false
ingest_schedule    = "cron(0 6 ? * MON *)"

# LLM models. Titan Embed Text v2 remains the retrieval embedding model.
# These are pay-per-query and do not add always-on cost.
bedrock_model_planner  = "amazon.nova-lite-v1:0"
bedrock_model_reranker = "amazon.nova-lite-v1:0"
bedrock_model_answer   = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
