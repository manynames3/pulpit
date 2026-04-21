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
  source            = "./modules/query"
  environment       = var.environment
  bedrock_model_id  = var.bedrock_model_id
  transcript_bucket = module.ingestion.transcript_bucket_name
  church_name       = var.church_name
  pastor_contact    = var.pastor_contact
}
