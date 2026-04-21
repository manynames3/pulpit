# Bedrock Knowledge Base with S3 vector store.
# 
# Why S3 Vector Store:
# OpenSearch Serverless (~$175/mo) is too expensive for this use case.
# By using S3 as the storage backend for vectors, we keep costs to ~$0. 

# 1. ADD THIS: A new bucket to store the actual vector indexes
resource "aws_s3_bucket" "vector_store" {
  bucket        = "pulpit-vectors-${var.environment}-${data.aws_caller_identity.current.account_id}"
  force_destroy = true 
  tags          = local.tags
}

resource "aws_iam_role" "bedrock_kb" {
  name = "pulpit-bedrock-kb-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "bedrock_kb" {
  name = "pulpit-bedrock-kb-policy-${var.environment}"
  role = aws_iam_role.bedrock_kb.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          var.s3_bucket_arn,
          "${var.s3_bucket_arn}/*"
        ]
      },
      # ADDED: Permissions for the new vector storage bucket
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:DeleteObject"]
        Resource = [
          aws_s3_bucket.vector_store.arn,
          "${aws_s3_bucket.vector_store.arn}/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"
      }
    ]
  })
}

resource "aws_bedrockagent_knowledge_base" "sermons" {
  name     = "pulpit-sermons-${var.environment}"
  role_arn = aws_iam_role.bedrock_kb.arn

  knowledge_base_configuration {
    type = "VECTOR"
    vector_knowledge_base_configuration {
      embedding_model_arn = "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"
    }
  }

  # UPDATED: Changed from BEDROCK_MANAGED_VECTOR_STORE to S3
  storage_configuration {
    type = "S3"
    s3_configuration {
      bucket_name = aws_s3_bucket.vector_store.id
    }
  }

  tags = local.tags
}

resource "aws_bedrockagent_data_source" "transcripts" {
  knowledge_base_id = aws_bedrockagent_knowledge_base.sermons.id
  name              = "sermon-transcripts"

  data_source_configuration {
    type = "S3"
    s3_configuration {
      bucket_arn = var.s3_bucket_arn
    }
  }

  vector_ingestion_configuration {
    chunking_configuration {
      chunking_strategy = "FIXED_SIZE"
      fixed_size_chunking_configuration {
        max_tokens         = 300
        overlap_percentage = 20
      }
    }
  }
}

locals {
  tags = {
    Project     = "pulpit"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# Need this for the bucket naming
data "aws_caller_identity" "current" {}
