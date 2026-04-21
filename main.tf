terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

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
  youtube_api_key    = var.youtube_api_key
  ingest_schedule    = var.ingest_schedule
}

module "knowledge_base" {
  source         = "./modules/knowledge-base"
  environment    = var.environment
  s3_bucket_arn  = module.ingestion.transcript_bucket_arn
  s3_bucket_name = module.ingestion.transcript_bucket_name
}

module "query" {
  source            = "./modules/query"
  environment       = var.environment
  bedrock_model_id  = var.bedrock_model_id
  knowledge_base_id = module.knowledge_base.knowledge_base_id
  church_name       = var.church_name
  pastor_contact    = var.pastor_contact
}
