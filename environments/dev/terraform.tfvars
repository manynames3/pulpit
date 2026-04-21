environment        = "dev"
aws_region         = "us-east-1"
church_name        = "Atlanta Bethel Church"
pastor_contact     = "Please contact our pastoral team at abc@atlbethel.org"
youtube_channel_id = "UCchY0Iagf_2cCP0RGVwQ-FA"
bedrock_model_id   = "amazon.nova-lite-v1:0"
enable_guardduty   = false
ingest_schedule    = "cron(0 6 ? * MON *)"
# youtube_api_key is stored in AWS SSM Parameter Store — not committed to git
# Path: /pulpit/dev/youtube_api_key
# Set via: aws ssm put-parameter --name "/pulpit/dev/youtube_api_key" --value "YOUR_KEY" --type SecureString
