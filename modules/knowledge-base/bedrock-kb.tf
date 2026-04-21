# Bedrock Knowledge Base — REMOVED for v1 pilot.
#
# Why removed:
# Every valid vector store option (OpenSearch Serverless, RDS, Pinecone, etc.)
# either has significant idle cost or introduces a third-party dependency.
# For the 2026-only pilot (~16 sermons), a vector database is overkill.
#
# v1 approach: Lambda loads transcript JSONs from S3 directly and passes
# relevant content to Claude. Costs ~$0 for storage, ~$0.003/query.
# Scales cleanly up to ~50 sermons before context becomes a concern.
#
# Upgrade path (when ready for full archive):
#   Option A — OpenSearch Serverless: best AWS-native, ~$175/month minimum
#   Option B — Pinecone free tier: 2GB free forever, data leaves AWS
#   Option C — RDS pgvector: free 12 months, then ~$15/month
#
# To enable: uncomment and configure the storage backend of your choice,
# then update the query Lambda to use RetrieveAndGenerate instead of
# direct S3 fetch.

# Placeholder outputs so other modules compile cleanly
locals {
  tags = {
    Project     = "pulpit"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
