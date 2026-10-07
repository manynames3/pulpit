# Use the existing AWS provider's CloudFormation resource so S3 Vectors does
# not require upgrading all of this repository's AWS infrastructure at once.
resource "aws_cloudformation_stack" "retrieval" {
  name = "pulpit-vectors-${var.environment}"
  template_body = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09"
    Resources = {
      VectorBucket = {
        Type                = "AWS::S3Vectors::VectorBucket"
        DeletionPolicy      = "Retain"
        UpdateReplacePolicy = "Retain"
        Properties = {
          VectorBucketName        = "pulpit-vectors-${var.environment}"
          EncryptionConfiguration = { SseType = "AES256" }
        }
      }
      ChunkIndex = {
        Type                = "AWS::S3Vectors::Index"
        DeletionPolicy      = "Retain"
        UpdateReplacePolicy = "Retain"
        Properties = {
          VectorBucketArn = { Ref = "VectorBucket" }
          IndexName       = "sermon-chunks-v1"
          DataType        = "float32"
          Dimension       = 256
          DistanceMetric  = "cosine"
        }
      }
    }
    Outputs = { IndexArn = { Value = { Ref = "ChunkIndex" } } }
  })
  tags = local.tags
}

output "vector_index_arn" {
  value = aws_cloudformation_stack.retrieval.outputs["IndexArn"]
}
