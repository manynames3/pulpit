data "aws_region" "current" {}

output "api_endpoint" {
  value = "https://${aws_api_gateway_rest_api.pulpit.id}.execute-api.${data.aws_region.current.name}.amazonaws.com/${var.environment}"
}

output "admin_ingest_endpoint" {
  value = "https://${aws_api_gateway_rest_api.pulpit.id}.execute-api.${data.aws_region.current.name}.amazonaws.com/${var.environment}/admin/ingest/run"
}

output "cognito_user_pool_id" { value = aws_cognito_user_pool.pulpit.id }
output "cognito_client_id" { value = aws_cognito_user_pool_client.pulpit.id }
output "admin_config_table_name" { value = aws_dynamodb_table.admin_config.name }
output "retrieval_eval_table_name" { value = aws_dynamodb_table.retrieval_eval.name }
