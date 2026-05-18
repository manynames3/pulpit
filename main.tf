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

# Knowledge Base module removed for v1 pilot — see modules/knowledge-base/bedrock-kb.tf
# Query Lambda reads directly from S3 for the 2026-only sermon set (~16 sermons)
# Re-enable when expanding to full archive and adding a vector store backend

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
