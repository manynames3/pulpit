output "api_endpoint" {
  description = "HTTPS endpoint for sermon queries."
  value       = module.query.api_endpoint
}

output "admin_ingest_endpoint" {
  description = "Staff-only endpoint for manual ingestion triggers."
  value       = module.query.admin_ingest_endpoint
}

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID."
  value       = module.query.cognito_user_pool_id
}

output "transcript_bucket_name" {
  description = "S3 bucket storing sermon transcripts."
  value       = module.ingestion.transcript_bucket_name
}

output "ingest_queue_url" {
  description = "SQS queue used for admin-triggered ingest jobs."
  value       = module.ingestion.ingest_queue_url
}

output "ingest_dlq_url" {
  description = "Dead-letter queue for failed ingest jobs."
  value       = module.ingestion.ingest_dlq_url
}

output "admin_config_table_name" {
  description = "DynamoDB table for admin-controlled retrieval settings."
  value       = module.query.admin_config_table_name
}

output "retrieval_eval_table_name" {
  description = "DynamoDB table storing retrieval-evaluation samples."
  value       = module.query.retrieval_eval_table_name
}
