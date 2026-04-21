environment        = "dev"
aws_region         = "us-east-1"
church_name        = "Atlanta Bethel Church"
pastor_contact     = "Please contact our pastoral team at abc@atlbethel.org"
youtube_channel_id = "UCchY0Iagf_2cCP0RGVwQ-FA"
enable_guardduty   = false
ingest_schedule    = "cron(0 6 ? * MON *)"

# LLM model — swap anytime, no code changes needed
# amazon.nova-lite-v1:0                   ~$0.06/1M tokens  ← default
# amazon.nova-pro-v1:0                    ~$0.80/1M tokens
# anthropic.claude-haiku-4-5-20251001     ~$0.80/1M tokens
# anthropic.claude-sonnet-4-6             ~$3.00/1M tokens
bedrock_model_id = "amazon.nova-lite-v1:0"
