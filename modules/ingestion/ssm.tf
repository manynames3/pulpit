# API key stored in SSM Parameter Store — never hardcoded, never committed to git.
# This is the correct pattern for secrets in AWS Lambda.
#
# To set the value after terraform apply:
#   aws ssm put-parameter \
#     --name "/pulpit/dev/youtube_api_key" \
#     --value "YOUR_API_KEY" \
#     --type SecureString \
#     --overwrite
#
# Lambda reads it at runtime via the AWS SDK — not injected as env var.

resource "aws_ssm_parameter" "youtube_api_key" {
  name        = "/pulpit/${var.environment}/youtube_api_key"
  description = "YouTube Data API v3 key for Pulpit sermon ingestion"
  type        = "SecureString"
  value       = "PLACEHOLDER — update via aws ssm put-parameter after deploy"

  lifecycle {
    # Prevent Terraform from overwriting the real value after initial deploy
    ignore_changes = [value]
  }

  tags = local.tags
}

# Grant ingest Lambda permission to read this specific parameter only
resource "aws_iam_role_policy" "ingest_ssm" {
  name = "pulpit-ingest-ssm-${var.environment}"
  role = aws_iam_role.ingest_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameter"]
      Resource = aws_ssm_parameter.youtube_api_key.arn
    }]
  })
}
