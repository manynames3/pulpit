# Query audit log — who asked what, when.
# Required for pastoral accountability and staff oversight.
# DynamoDB free tier: 25GB storage + 25 read/write units — covers any church at this scale.

resource "aws_dynamodb_table" "query_log" {
  name         = "pulpit-queries-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "queryId"
  range_key    = "timestamp"

  attribute {
    name = "queryId"
    type = "S"
  }

  attribute {
    name = "timestamp"
    type = "S"
  }

  # Retention: 90 days dev, 1 year prod
  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }

  deletion_protection_enabled = var.environment == "prod" ? true : false

  tags = local.tags
}

# Query answer cache — identical questions return instantly at zero cost.
# Key: SHA256(question) → cached answer + 30-day TTL.
# Common church questions (grace, resurrection, baptism) will hit cache constantly.

resource "aws_dynamodb_table" "query_cache" {
  name         = "pulpit-cache-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "questionHash"

  attribute {
    name = "questionHash"
    type = "S"
  }

  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }

  tags = local.tags
}

# Admin-controlled retrieval configuration.
# Example item:
# configKey=retrieval, version=v1, synonyms={...}, preferredSermons=[...], hiddenSermons=[...]

resource "aws_dynamodb_table" "admin_config" {
  name         = "pulpit-admin-config-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "configKey"

  attribute {
    name = "configKey"
    type = "S"
  }

  deletion_protection_enabled = var.environment == "prod" ? true : false

  tags = local.tags
}

# Retrieval evaluation samples for improving relevance over time.
# This is intentionally lightweight: one row per query with top matched chunks.

resource "aws_dynamodb_table" "retrieval_eval" {
  name         = "pulpit-retrieval-eval-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "evalId"
  range_key    = "timestamp"

  attribute {
    name = "evalId"
    type = "S"
  }

  attribute {
    name = "timestamp"
    type = "S"
  }

  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }

  tags = local.tags
}
