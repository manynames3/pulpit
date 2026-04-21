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
