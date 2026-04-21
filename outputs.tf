output "api_endpoint" {
  description = "HTTPS endpoint for sermon queries."
  value       = module.query.api_endpoint
}

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID."
  value       = module.query.cognito_user_pool_id
}

output "transcript_bucket_name" {
  description = "S3 bucket storing sermon transcripts."
  value       = module.ingestion.transcript_bucket_name
}
