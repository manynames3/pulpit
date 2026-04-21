# Bedrock Knowledge Base manages chunking, embedding, and vector storage internally.
# No OpenSearch cluster needed — saves ~$90/month base cost at this scale.
# At >50k queries/month, consider migrating to dedicated OpenSearch Serverless.

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
      # Amazon Titan Embeddings — included in Bedrock, no extra cost
      embedding_model_arn = "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"
    }
  }

  storage_configuration {
    type = "OPENSEARCH_SERVERLESS"
    opensearch_serverless_configuration {
      collection_arn    = aws_opensearchserverless_collection.pulpit.arn
      vector_index_name = "pulpit-sermons"
      field_mapping {
        vector_field   = "embedding"
        text_field     = "text"
        metadata_field = "metadata"
      }
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
}

# OpenSearch Serverless — managed vector store.
# No persistent cluster = scales to zero when idle.
resource "aws_opensearchserverless_collection" "pulpit" {
  name = "pulpit-${var.environment}"
  type = "VECTORSEARCH"
  tags = local.tags
}

resource "aws_opensearchserverless_access_policy" "pulpit" {
  name = "pulpit-access-${var.environment}"
  type = "data"
  policy = jsonencode([{
    Rules = [
      {
        ResourceType = "index"
        Resource     = ["index/pulpit-${var.environment}/*"]
        Permission   = ["aoss:*"]
      },
      {
        ResourceType = "collection"
        Resource     = ["collection/pulpit-${var.environment}"]
        Permission   = ["aoss:*"]
      }
    ]
    Principal = [aws_iam_role.bedrock_kb.arn]
  }])
}

resource "aws_opensearchserverless_security_policy" "encryption" {
  name = "pulpit-enc-${var.environment}"
  type = "encryption"
  policy = jsonencode({
    Rules = [{
      ResourceType = "collection"
      Resource     = ["collection/pulpit-${var.environment}"]
    }]
    AWSOwnedKey = true
  })
}

resource "aws_opensearchserverless_security_policy" "network" {
  name = "pulpit-net-${var.environment}"
  type = "network"
  policy = jsonencode([{
    Rules = [
      {
        ResourceType = "collection"
        Resource     = ["collection/pulpit-${var.environment}"]
      },
      {
        ResourceType = "dashboard"
        Resource     = ["collection/pulpit-${var.environment}"]
      }
    ]
    AllowFromPublic = false
  }])
}

locals {
  tags = {
    Project     = "pulpit"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
