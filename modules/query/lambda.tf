data "archive_file" "query" {
  type        = "zip"
  source_dir  = "${path.root}/lambda/query"
  output_path = "${path.module}/query.zip"
}

resource "aws_lambda_function" "query" {
  function_name    = "pulpit-query-${var.environment}"
  role             = aws_iam_role.query_lambda.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.query.output_path
  source_code_hash = data.archive_file.query.output_base64sha256
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      BEDROCK_MODEL_ID  = var.bedrock_model_id
      KNOWLEDGE_BASE_ID = var.knowledge_base_id
      GUARDRAIL_ID      = aws_bedrock_guardrail.pulpit.guardrail_id
      GUARDRAIL_VERSION = aws_bedrock_guardrail.pulpit.version
      DYNAMODB_TABLE    = aws_dynamodb_table.query_log.name
      PASTOR_CONTACT    = var.pastor_contact
      ENVIRONMENT       = var.environment
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

resource "aws_iam_role_policy" "query_lambda" {
  name = "pulpit-query-policy-${var.environment}"
  role = aws_iam_role.query_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["bedrock:RetrieveAndGenerate", "bedrock:Retrieve", "bedrock:InvokeModel"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:ApplyGuardrail"]
        Resource = aws_bedrock_guardrail.pulpit.guardrail_arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = aws_dynamodb_table.query_log.arn
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
