environment        = "prod"
aws_region         = "us-east-1"
church_name        = "Atlanta Bethel Church"
pastor_contact     = "Please contact our pastoral team at abc@atlbethel.org"
youtube_channel_id = "UCchY0Iagf_2cCP0RGVwQ-FA"
enable_guardduty   = true
ingest_schedule    = "cron(0 6 ? * MON *)"

# LLM answer model. Titan Embed Text v2 remains the retrieval embedding model.
# This inference profile is pay-per-query and does not add always-on cost.
bedrock_model_id = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
