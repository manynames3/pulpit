environment        = "prod"
aws_region         = "us-east-1"
church_name        = "Atlanta Bethel Church"
pastor_contact     = "Please contact our pastoral team at church@atlantabethel.org"
youtube_channel_id = "YOUR_YOUTUBE_CHANNEL_ID"
youtube_api_key    = "YOUR_YOUTUBE_API_KEY"

# Upgrade model for production quality
# Options: amazon.nova-pro-v1:0 | anthropic.claude-haiku-4-5-20251001 | anthropic.claude-sonnet-4-6
bedrock_model_id   = "amazon.nova-pro-v1:0"

# Enable threat detection in production
enable_guardduty   = true

ingest_schedule    = "cron(0 6 ? * MON *)"
