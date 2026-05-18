data "archive_file" "query" {
  type        = "zip"
  source_dir  = "${path.module}/../../lambda/query/package"
  output_path = "${path.module}/query.zip"
}

data "archive_file" "admin_trigger" {
  type        = "zip"
  source_dir  = "${path.module}/../../lambda/admin-trigger/package"
  output_path = "${path.module}/admin-trigger.zip"
}

resource "aws_lambda_function" "query" {
  function_name    = "pulpit-query-${var.environment}"
  role             = aws_iam_role.query_lambda.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.query.output_path
  source_code_hash = data.archive_file.query.output_base64sha256
  timeout          = 60  # API Gateway cuts at 29s; Lambda gets 60s for non-API invocations
  memory_size      = 512 # bumped from 256 — cosine similarity over 500 embeddings needs headroom

  environment {
    variables = {
      BEDROCK_MODEL_ID  = var.bedrock_model_id
      TRANSCRIPT_BUCKET = var.transcript_bucket
      GUARDRAIL_ID      = aws_bedrock_guardrail.pulpit.guardrail_id
      GUARDRAIL_VERSION = aws_bedrock_guardrail.pulpit.version
      DYNAMODB_TABLE    = aws_dynamodb_table.query_log.name
      CACHE_TABLE       = aws_dynamodb_table.query_cache.name
      CONFIG_TABLE      = aws_dynamodb_table.admin_config.name
      EVAL_TABLE        = aws_dynamodb_table.retrieval_eval.name
      PASTOR_CONTACT    = var.pastor_contact
      ENVIRONMENT       = var.environment
    }
  }

  tags = local.tags
}

resource "aws_lambda_function" "admin_trigger" {
  function_name    = "pulpit-admin-trigger-${var.environment}"
  role             = aws_iam_role.admin_trigger_lambda.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.admin_trigger.output_path
  source_code_hash = data.archive_file.admin_trigger.output_base64sha256
  timeout          = 10
  memory_size      = 128

  environment {
    variables = {
      INGEST_QUEUE_URL = var.ingest_queue_url
      ADMIN_GROUPS     = var.admin_group_names
      ENVIRONMENT      = var.environment
    }
  }

  tags = local.tags
}

resource "aws_iam_role" "query_lambda" {
  name = "pulpit-query-lambda-${var.environment}"

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

resource "aws_iam_role" "admin_trigger_lambda" {
  name = "pulpit-admin-trigger-lambda-${var.environment}"

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

resource "aws_iam_role_policy" "admin_trigger_lambda" {
  name = "pulpit-admin-trigger-policy-${var.environment}"
  role = aws_iam_role.admin_trigger_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = var.ingest_queue_arn
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

resource "aws_iam_role_policy" "query_lambda" {
  name = "pulpit-query-policy-${var.environment}"
  role = aws_iam_role.query_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Read index.json + individual transcripts from S3
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          "arn:aws:s3:::pulpit-transcripts-${var.environment}-*",
          "arn:aws:s3:::pulpit-transcripts-${var.environment}-*/*"
        ]
      },
      {
        # Bedrock answer model + Titan Embed Text v2 for semantic retrieval.
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "*"
      },
      {
        # Apply guardrails to generated responses.
        Effect   = "Allow"
        Action   = ["bedrock:ApplyGuardrail"]
        Resource = aws_bedrock_guardrail.pulpit.guardrail_arn
      },
      {
        # Audit log writes
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = aws_dynamodb_table.query_log.arn
      },
      {
        # Cache reads + writes
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem"]
        Resource = aws_dynamodb_table.query_cache.arn
      },
      {
        # Admin retrieval tuning reads
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem"]
        Resource = aws_dynamodb_table.admin_config.arn
      },
      {
        # Retrieval evaluation writes
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = aws_dynamodb_table.retrieval_eval.arn
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
