resource "aws_api_gateway_rest_api" "pulpit" {
  name        = "pulpit-api-${var.environment}"
  description = "Pulpit sermon query API for ${var.environment}"
  tags        = local.tags
}

resource "aws_api_gateway_resource" "query" {
  rest_api_id = aws_api_gateway_rest_api.pulpit.id
  parent_id   = aws_api_gateway_rest_api.pulpit.root_resource_id
  path_part   = "query"
}

resource "aws_api_gateway_method" "query_post" {
  rest_api_id   = aws_api_gateway_rest_api.pulpit.id
  resource_id   = aws_api_gateway_resource.query.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_authorizer" "cognito" {
  name          = "pulpit-cognito-authorizer"
  rest_api_id   = aws_api_gateway_rest_api.pulpit.id
  type          = "COGNITO_USER_POOLS"
  provider_arns = [aws_cognito_user_pool.pulpit.arn]
}

resource "aws_api_gateway_integration" "query_lambda" {
  rest_api_id             = aws_api_gateway_rest_api.pulpit.id
  resource_id             = aws_api_gateway_resource.query.id
  http_method             = aws_api_gateway_method.query_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.query.invoke_arn
}

resource "aws_api_gateway_deployment" "pulpit" {
  depends_on  = [aws_api_gateway_integration.query_lambda]
  rest_api_id = aws_api_gateway_rest_api.pulpit.id
  stage_name  = var.environment
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.query.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.pulpit.execution_arn}/*/*"
}
