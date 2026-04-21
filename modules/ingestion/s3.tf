resource "aws_s3_bucket" "transcripts" {
  bucket        = "pulpit-transcripts-${var.environment}-${data.aws_caller_identity.current.account_id}"
  force_destroy = var.environment == "dev" ? true : false
  tags          = local.tags
}

resource "aws_s3_bucket_public_access_block" "transcripts" {
  bucket                  = aws_s3_bucket.transcripts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Default encryption — free, sufficient for non-profit pilot.
# Upgrade to KMS CMK in prod for full key control and rotation audit trail.
resource "aws_s3_bucket_server_side_encryption_configuration" "transcripts" {
  bucket = aws_s3_bucket.transcripts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "transcripts" {
  bucket = aws_s3_bucket.transcripts.id
  versioning_configuration {
    status = "Enabled"
  }
}

data "aws_caller_identity" "current" {}
