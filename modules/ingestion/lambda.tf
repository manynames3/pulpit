data "archive_file" "ingest" {
  type        = "zip"
  source_dir  = "${path.module}/../../lambda/ingest/package"
  output_path = "${path.module}/ingest.zip"
}

resource "aws_sqs_queue" "ingest_dlq" {
  name                      = "pulpit-ingest-dlq-${var.environment}"
  message_retention_seconds = 1209600

  tags = local.tags
}

resource "aws_sqs_queue" "ingest_queue" {
  name                       = "pulpit-ingest-${var.environment}"
  visibility_timeout_seconds = 960
  message_retention_seconds  = 1209600
  receive_wait_time_seconds  = 10

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = 2
  })

  tags = local.tags
}

resource "aws_lambda_function" "ingest" {
  function_name    = "pulpit-ingest-${var.environment}"
  role             = aws_iam_role.ingest_lambda.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.ingest.output_path
  source_code_hash = data.archive_file.ingest.output_base64sha256
  timeout          = 900
  memory_size      = 512

  environment {
    variables = {
      YOUTUBE_CHANNEL_ID = var.youtube_channel_id
      TRANSCRIPT_BUCKET  = aws_s3_bucket.transcripts.bucket
      # SSM path — Lambda fetches the actual key value at runtime
      # API key is never stored in env vars, code, or git
      SSM_PARAMETER_NAME = aws_ssm_parameter.youtube_api_key.name
      ENVIRONMENT        = var.environment
    }
  }

  tags = local.tags
}

resource "aws_lambda_event_source_mapping" "ingest_queue" {
  event_source_arn                   = aws_sqs_queue.ingest_queue.arn
  function_name                      = aws_lambda_function.ingest.arn
  batch_size                         = 1
  maximum_batching_window_in_seconds = 0
  enabled                            = true

  depends_on = [aws_iam_role_policy.ingest_lambda]
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
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:GetObject", "s3:HeadObject"]
        Resource = [
          "${aws_s3_bucket.transcripts.arn}/transcripts/*",
          "${aws_s3_bucket.transcripts.arn}/sermons/*",
          "${aws_s3_bucket.transcripts.arn}/indexes/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.transcripts.arn
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = aws_ssm_parameter.youtube_api_key.arn
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility"
        ]
        Resource = aws_sqs_queue.ingest_queue.arn
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
