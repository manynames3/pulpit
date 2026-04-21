output "api_endpoint" { value = aws_api_gateway_deployment.pulpit.invoke_url }
output "cognito_user_pool_id" { value = aws_cognito_user_pool.pulpit.id }
output "cognito_client_id" { value = aws_cognito_user_pool_client.pulpit.id }
