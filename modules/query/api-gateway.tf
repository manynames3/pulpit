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

resource "aws_api_gateway_resource" "catalog" {
  rest_api_id = aws_api_gateway_rest_api.pulpit.id
  parent_id   = aws_api_gateway_rest_api.pulpit.root_resource_id
  path_part   = "catalog"
}

resource "aws_api_gateway_resource" "admin" {
  rest_api_id = aws_api_gateway_rest_api.pulpit.id
  parent_id   = aws_api_gateway_rest_api.pulpit.root_resource_id
  path_part   = "admin"
}

resource "aws_api_gateway_resource" "admin_ingest" {
  rest_api_id = aws_api_gateway_rest_api.pulpit.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "ingest"
}

resource "aws_api_gateway_resource" "admin_ingest_run" {
  rest_api_id = aws_api_gateway_rest_api.pulpit.id
  parent_id   = aws_api_gateway_resource.admin_ingest.id
  path_part   = "run"
}

# POST: authenticated sermon query
resource "aws_api_gateway_method" "query_post" {
  rest_api_id   = aws_api_gateway_rest_api.pulpit.id
  resource_id   = aws_api_gateway_resource.query.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

# GET: authenticated catalog payload for the browser
resource "aws_api_gateway_method" "catalog_get" {
  rest_api_id   = aws_api_gateway_rest_api.pulpit.id
  resource_id   = aws_api_gateway_resource.catalog.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

# POST: staff/admin-only manual ingestion trigger
resource "aws_api_gateway_method" "admin_ingest_run_post" {
  rest_api_id   = aws_api_gateway_rest_api.pulpit.id
  resource_id   = aws_api_gateway_resource.admin_ingest_run.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

# OPTIONS: CORS preflight, no auth required
resource "aws_api_gateway_method" "query_options" {
  rest_api_id   = aws_api_gateway_rest_api.pulpit.id
  resource_id   = aws_api_gateway_resource.query.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_method" "catalog_options" {
  rest_api_id   = aws_api_gateway_rest_api.pulpit.id
  resource_id   = aws_api_gateway_resource.catalog.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_method" "admin_ingest_run_options" {
  rest_api_id   = aws_api_gateway_rest_api.pulpit.id
  resource_id   = aws_api_gateway_resource.admin_ingest_run.id
  http_method   = "OPTIONS"
  authorization = "NONE"
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

resource "aws_api_gateway_integration" "catalog_lambda" {
  rest_api_id             = aws_api_gateway_rest_api.pulpit.id
  resource_id             = aws_api_gateway_resource.catalog.id
  http_method             = aws_api_gateway_method.catalog_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.query.invoke_arn
}

resource "aws_api_gateway_integration" "admin_trigger_lambda" {
  rest_api_id             = aws_api_gateway_rest_api.pulpit.id
  resource_id             = aws_api_gateway_resource.admin_ingest_run.id
  http_method             = aws_api_gateway_method.admin_ingest_run_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.admin_trigger.invoke_arn
}

# OPTIONS integration: mock response with CORS headers
resource "aws_api_gateway_integration" "query_options" {
  rest_api_id = aws_api_gateway_rest_api.pulpit.id
  resource_id = aws_api_gateway_resource.query.id
  http_method = aws_api_gateway_method.query_options.http_method
  type        = "MOCK"

  request_templates = {
    "application/json" = "{\"statusCode\": 200}"
  }
}

resource "aws_api_gateway_integration" "catalog_options" {
  rest_api_id = aws_api_gateway_rest_api.pulpit.id
  resource_id = aws_api_gateway_resource.catalog.id
  http_method = aws_api_gateway_method.catalog_options.http_method
  type        = "MOCK"

  request_templates = {
    "application/json" = "{\"statusCode\": 200}"
  }
}

resource "aws_api_gateway_integration" "admin_ingest_run_options" {
  rest_api_id = aws_api_gateway_rest_api.pulpit.id
  resource_id = aws_api_gateway_resource.admin_ingest_run.id
  http_method = aws_api_gateway_method.admin_ingest_run_options.http_method
  type        = "MOCK"

  request_templates = {
    "application/json" = "{\"statusCode\": 200}"
  }
}

resource "aws_api_gateway_method_response" "query_options_200" {
  rest_api_id = aws_api_gateway_rest_api.pulpit.id
  resource_id = aws_api_gateway_resource.query.id
  http_method = aws_api_gateway_method.query_options.http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_method_response" "catalog_options_200" {
  rest_api_id = aws_api_gateway_rest_api.pulpit.id
  resource_id = aws_api_gateway_resource.catalog.id
  http_method = aws_api_gateway_method.catalog_options.http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_method_response" "admin_ingest_run_options_200" {
  rest_api_id = aws_api_gateway_rest_api.pulpit.id
  resource_id = aws_api_gateway_resource.admin_ingest_run.id
  http_method = aws_api_gateway_method.admin_ingest_run_options.http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "query_options" {
  rest_api_id = aws_api_gateway_rest_api.pulpit.id
  resource_id = aws_api_gateway_resource.query.id
  http_method = aws_api_gateway_method.query_options.http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
    "method.response.header.Access-Control-Allow-Methods" = "'POST,OPTIONS'"
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
  }

  depends_on = [aws_api_gateway_integration.query_options]
}

resource "aws_api_gateway_integration_response" "catalog_options" {
  rest_api_id = aws_api_gateway_rest_api.pulpit.id
  resource_id = aws_api_gateway_resource.catalog.id
  http_method = aws_api_gateway_method.catalog_options.http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
    "method.response.header.Access-Control-Allow-Methods" = "'GET,OPTIONS'"
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
  }

  depends_on = [aws_api_gateway_integration.catalog_options]
}

resource "aws_api_gateway_integration_response" "admin_ingest_run_options" {
  rest_api_id = aws_api_gateway_rest_api.pulpit.id
  resource_id = aws_api_gateway_resource.admin_ingest_run.id
  http_method = aws_api_gateway_method.admin_ingest_run_options.http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
    "method.response.header.Access-Control-Allow-Methods" = "'POST,OPTIONS'"
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
  }

  depends_on = [aws_api_gateway_integration.admin_ingest_run_options]
}

resource "aws_api_gateway_deployment" "pulpit" {
  depends_on = [
    aws_api_gateway_integration.query_lambda,
    aws_api_gateway_integration.catalog_lambda,
    aws_api_gateway_integration.admin_trigger_lambda,
    aws_api_gateway_integration_response.query_options,
    aws_api_gateway_integration_response.catalog_options,
    aws_api_gateway_integration_response.admin_ingest_run_options
  ]
  rest_api_id = aws_api_gateway_rest_api.pulpit.id
  stage_name  = var.environment

  triggers = {
    redeploy = sha1(jsonencode([
      aws_api_gateway_resource.query.id,
      aws_api_gateway_resource.catalog.id,
      aws_api_gateway_resource.admin_ingest_run.id,
      aws_api_gateway_method.query_post.id,
      aws_api_gateway_method.catalog_get.id,
      aws_api_gateway_method.admin_ingest_run_post.id,
      aws_api_gateway_integration.query_lambda.id,
      aws_api_gateway_integration.catalog_lambda.id,
      aws_api_gateway_integration.admin_trigger_lambda.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.query.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.pulpit.execution_arn}/*/*"
}

resource "aws_lambda_permission" "api_gateway_admin_trigger" {
  statement_id  = "AllowAPIGatewayInvokeAdminTrigger"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.admin_trigger.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.pulpit.execution_arn}/*/*"
}
