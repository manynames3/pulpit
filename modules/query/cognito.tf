resource "aws_cognito_user_pool" "pulpit" {
  name = "pulpit-${var.environment}"

  # Signup is email-based, so Cognito needs an email attribute to verify and recover accounts.
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 8
    require_uppercase = true
    require_numbers   = true
    require_symbols   = false
  }

  # Staff require MFA in prod — members optional
  mfa_configuration = var.environment == "prod" ? "ON" : "OPTIONAL"

  software_token_mfa_configuration {
    enabled = true
  }

  tags = local.tags
}

resource "aws_cognito_user_pool_client" "pulpit" {
  name         = "pulpit-client-${var.environment}"
  user_pool_id = aws_cognito_user_pool.pulpit.id

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH"
  ]
}

# Two tiers: member (sermon access) and staff (full access including board docs)
resource "aws_cognito_user_group" "member" {
  name         = "member"
  user_pool_id = aws_cognito_user_pool.pulpit.id
  description  = "General congregation — sermon archive access only"
}

resource "aws_cognito_user_group" "staff" {
  name         = "staff"
  user_pool_id = aws_cognito_user_pool.pulpit.id
  description  = "Staff and elders — full access including board minutes and pastoral policies"
}
