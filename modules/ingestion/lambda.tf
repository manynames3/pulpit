data "archive_file" "ingest" {
  type        = "zip"
  source_dir  = "${path.root}/lambda/ingest"
  output_path = "${path.module}/ingest.zip"
}

resource "aws_lambda_function" "ingest" {
  function_name    = "pulpit-ingest-${var.environment}"
  role             = aws_iam_role.ingest_lambda.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.ingest.output_path
  source_code_hash = data.archive_file.ingest.output_base64sha256
  timeout          = 300
  memory_size      = 256

  environment {
    variables = {
      YOUTUBE_CHANNEL_ID  = var.youtube_channel_id
      TRANSCRIPT_BUCKET   = aws_s3_bucket.transcripts.bucket
      # SSM path — Lambda fetches the actual key value at runtime
      # API key is never stored in env vars, code, or git
      SSM_PARAMETER_NAME  = aws_ssm_parameter.youtube_api_key.name
      ENVIRONMENT         = var.environment
    }
  }

  tags = local.tags
}

resource "aws_iam_role" "ingest_lambda" {
  name = "pulpit-ingest-lambda-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "ingest_lambda" {
  name = "pulpit-ingest-policy-${var.environment}"
  role = aws_iam_role.ingest_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject", "s3:HeadObject"]
        Resource = "${aws_s3_bucket.transcripts.arn}/transcripts/*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

locals {
  tags = {
    Project     = "pulpit"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
