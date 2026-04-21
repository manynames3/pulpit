data "aws_region" "current" {}

output "api_endpoint" {
  value = "https://${aws_api_gateway_rest_api.pulpit.id}.execute-api.${data.aws_region.current.name}.amazonaws.com/${var.environment}"
}

output "cognito_user_pool_id" { value = aws_cognito_user_pool.pulpit.id }
output "cognito_client_id" { value = aws_cognito_user_pool_client.pulpit.id }
