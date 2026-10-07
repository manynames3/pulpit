provider "aws" {
  region = var.aws_region
}

module "security" {
  source           = "./modules/security"
  environment      = var.environment
  enable_guardduty = var.enable_guardduty
}

module "ingestion" {
  source             = "./modules/ingestion"
  environment        = var.environment
  youtube_channel_id = var.youtube_channel_id
  ingest_schedule    = var.ingest_schedule
}

# Custom hybrid retrieval uses S3 Vectors and sharded lexical snapshots.
# The experimental Bedrock Knowledge Base module remains inactive.

module "query" {
  source                 = "./modules/query"
  environment            = var.environment
  bedrock_model_planner  = var.bedrock_model_planner
  bedrock_model_reranker = var.bedrock_model_reranker
  bedrock_model_answer   = var.bedrock_model_answer
  transcript_bucket      = module.ingestion.transcript_bucket_name
  ingest_lambda_arn      = module.ingestion.ingest_lambda_arn
  ingest_lambda_name     = module.ingestion.ingest_lambda_name
  ingest_queue_arn       = module.ingestion.ingest_queue_arn
  ingest_queue_url       = module.ingestion.ingest_queue_url
  church_name            = var.church_name
  pastor_contact         = var.pastor_contact
}
