# Security

This document describes the security model implemented in the repo and the gaps that remain. It does not claim external compliance certification or a completed production security review.

## Identity and Authorization

- Cognito user pools provide user authentication.
- API Gateway uses a Cognito user pool authorizer for:
  - `POST /query`
  - `GET /catalog`
  - `POST /admin/ingest/run`
- Cognito groups separate general users from staff/admin workflows.
- The admin ingest trigger Lambda checks `cognito:groups` before sending SQS messages.
- Browser clients do not receive AWS credentials, SQS permissions, Lambda invoke permissions, or the YouTube API key.

Relevant files:

- `modules/query/cognito.tf`
- `modules/query/api-gateway.tf`
- `lambda/admin-trigger/handler.py`

## IAM Approach

Lambda roles are defined per function. Policies grant the actions each function needs for its boundary:

- Query Lambda:
  - read transcript/index objects from S3
  - invoke Bedrock models
  - apply the configured Bedrock Guardrail
  - read/write the cache table
  - write the query audit table
  - read admin retrieval config
  - write retrieval eval samples
  - write CloudWatch logs
- Admin trigger Lambda:
  - send SQS messages
  - write CloudWatch logs
- Ingest Lambda:
  - read/write transcript objects
  - read the specific SSM parameter
  - invoke Bedrock
  - consume the ingest queue
  - write CloudWatch logs

The policies avoid `Action: "*"` and full administrative permissions. Some AWS service resources remain broad where Bedrock model ARNs or CloudWatch log resources are not narrowly specified.

Relevant files:

- `modules/query/lambda.tf`
- `modules/ingestion/lambda.tf`
- `modules/ingestion/ssm.tf`

## Secrets Management

- The AWS-managed YouTube API key path is an SSM SecureString parameter.
- Terraform creates a placeholder value and ignores future value changes.
- The real secret is set after deploy with `aws ssm put-parameter`.
- Local ingestion secrets are stored outside git in `.env` or `~/.config/pulpit-ingest.env`.
- `.gitignore` excludes `.env`, Terraform state, `cookies.txt`, and local scratch artifacts.

Relevant files:

- `modules/ingestion/ssm.tf`
- `scripts/pulpit-ingest.env.example`
- `.gitignore`

## Data Protection

- S3 transcript and CloudTrail buckets block public access.
- Transcript bucket has default AES256 server-side encryption.
- Transcript bucket has versioning enabled.
- CloudTrail log file validation is enabled.
- DynamoDB tables use AWS-managed service encryption by default.
- Query logs and retrieval eval records use TTL.

Relevant files:

- `modules/ingestion/s3.tf`
- `modules/security/cloudtrail.tf`
- `modules/query/dynamodb.tf`

## Network Boundaries

- The frontend is public static content on Cloudflare Pages.
- API Gateway is the public backend entry point.
- API routes require Cognito authorization except CORS preflight.
- Lambda functions are not placed in a VPC because the system uses public AWS service APIs and does not access private databases.
- There is no WAF configured in this repo.

## Prompt and Content Safety

- Bedrock Guardrails are provisioned with content filters and denied topics.
- Query code constrains answers to sermon content and returns fallback messages when evidence is weak.
- Guardrails are not a substitute for human pastoral judgment.

Relevant files:

- `modules/query/guardrails.tf`
- `lambda/query/query_service.py`

## Audit Logging

- Query/audit records are written to DynamoDB.
- CloudTrail captures AWS API activity to S3.
- CloudWatch stores Lambda/API logs.

Relevant files:

- `lambda/query/query_service.py`
- `modules/query/dynamodb.tf`
- `modules/security/cloudtrail.tf`

## Known Security Gaps

- API Gateway CORS currently uses `Access-Control-Allow-Origin: *`; production should restrict this to the final Cloudflare/custom domain.
- No WAF is configured.
- No custom CloudWatch alarms are codified for auth failures, API 5xx, DLQ depth, or suspicious usage.
- S3 uses AWS-managed AES256 encryption rather than a customer-managed KMS key.
- Checkov is soft-fail in CI, so it provides visibility rather than blocking merges.
- No formal penetration test or third-party security review has been performed.

## Security Improvement Backlog

- Restrict CORS origins per environment.
- Add WAF/rate limiting for the API Gateway or Cloudflare edge.
- Add CloudWatch alarms for auth failures, API errors, Lambda errors, DLQ depth, and unexpected Bedrock usage.
- Consider customer-managed KMS keys for transcript and CloudTrail buckets in prod.
- Make security scanning a hard gate after triaging current findings.
