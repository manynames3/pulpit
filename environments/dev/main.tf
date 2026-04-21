# Dev environment entry point.
# Run from this directory: terraform init && terraform plan

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.31"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Upgrade to S3 backend for team use:
  # backend "s3" {
  #   bucket = "pulpit-tfstate"
  #   key    = "dev/terraform.tfstate"
  #   region = "us-east-1"
  # }
}

provider "aws" {
  region = var.aws_region
}

# Point to root modules
module "pulpit" {
  source = "../.."

  environment        = var.environment
  aws_region         = var.aws_region
  church_name        = var.church_name
  pastor_contact     = var.pastor_contact
  youtube_channel_id = var.youtube_channel_id
  bedrock_model_id   = var.bedrock_model_id
  enable_guardduty   = var.enable_guardduty
  ingest_schedule    = var.ingest_schedule
}

variable "environment" { default = "dev" }
variable "aws_region" { default = "us-east-1" }
variable "church_name" { default = "Atlanta Bethel Church" }
variable "pastor_contact" {}
variable "youtube_channel_id" {}
variable "bedrock_model_id" { default = "amazon.nova-lite-v1:0" }
variable "enable_guardduty" { default = false }
variable "ingest_schedule" { default = "cron(0 6 ? * MON *)" }
