variable "environment" {}
variable "bedrock_model_id" { default = "amazon.nova-lite-v1:0" }
variable "transcript_bucket" {}
variable "ingest_lambda_arn" {}
variable "ingest_lambda_name" {}
variable "ingest_queue_arn" {}
variable "ingest_queue_url" {}
variable "admin_group_names" { default = "staff,admin" }
variable "church_name" { default = "Atlanta Bethel Church" }
variable "pastor_contact" {}
