# Bedrock Knowledge Base with native vector store.
#
# Why not OpenSearch Serverless:
# OpenSearch Serverless has a minimum charge of ~$175/month regardless
# of usage — completely inappropriate for a non-profit church deployment.
#
# Bedrock's native vector store (BEDROCK_MANAGED) has zero idle cost.
# You pay only for:
#   - Embedding at ingest: ~$0.10 per sermon (one-time, using Titan Embeddings)
#   - Queries: fractions of a cent per search
#
# Upgrade path: migrate to OpenSearch Serverless only if query volume
# exceeds ~50,000/month and you need advanced vector search features.

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
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "*"
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
      # Titan Embeddings v2 — included in Bedrock, no extra cost beyond
      # the per-token embedding charge (~$0.10 per sermon, one-time)
      embedding_model_arn = "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"
    }
  }

  # BEDROCK_MANAGED = AWS manages the vector store internally.
  # Zero idle cost. No OpenSearch cluster. No $175/month minimum.
  storage_configuration {
    type = "BEDROCK_MANAGED_VECTOR_STORE"
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

  # Chunking strategy: fixed size with overlap.
  # 300 tokens per chunk = roughly one sermon paragraph.
  # 20% overlap ensures context isn't lost at chunk boundaries.
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
