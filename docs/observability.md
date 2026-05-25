# Observability

Pulpit has basic AWS-native observability and audit records. It does not yet have custom dashboards or alarms codified in Terraform.

## What Exists

| Signal | Where |
|---|---|
| Lambda logs | CloudWatch Logs for query, ingest, and admin-trigger functions |
| API execution/service metrics | API Gateway and CloudWatch service metrics |
| Query audit records | DynamoDB `pulpit-queries-*` |
| Answer and intermediate cache records | DynamoDB `pulpit-cache-*` |
| Retrieval eval samples | DynamoDB `pulpit-retrieval-eval-*` |
| AWS API activity | CloudTrail writing to S3 |
| Ingest failures | SQS DLQ for AWS ingest queue |
| Local ingest run output | Local script stdout/stderr and cron/launchd logs |

## Query Path Signals

The query Lambda prints operational breadcrumbs for:

- index loading
- cache hits and misses
- index marker changes
- query expansion
- retrieval scores and thresholds
- reranker cache usage
- Bedrock and cache errors

It also writes query audit records with:

- user id
- user group
- question
- answer
- question type
- subquery count
- timestamp
- TTL

Relevant files:

- `lambda/query/query_service.py`
- `modules/query/dynamodb.tf`

## Ingestion Signals

Local ingestion reports:

- year scope
- videos found
- skipped/existing videos
- transcript failures
- YouTube IP block detection
- uploaded transcript keys
- index rebuild counts

AWS ingest has:

- SQS queue and DLQ
- Lambda logs
- EventBridge schedule

Relevant files:

- `scripts/ingest-local.py`
- `scripts/rebuild_index.py`
- `lambda/ingest/handler.py`
- `modules/ingestion/lambda.tf`

## Audit and Forensics

- CloudTrail records AWS API activity to an S3 bucket.
- Log file validation is enabled.
- Screenshots in `docs/screenshots/` show CloudTrail and DynamoDB resources that existed when captured.

Relevant files:

- `modules/security/cloudtrail.tf`
- `docs/screenshots/README.md`

## Recommended Alarms

These are not yet implemented in Terraform:

- Query Lambda error count greater than zero over a short window.
- Query Lambda duration approaching API Gateway timeout.
- API Gateway 5xx rate.
- API Gateway 4xx spike, especially auth-related failures.
- Ingest DLQ message count greater than zero.
- DynamoDB throttles or system errors.
- Bedrock invocation errors.
- CloudTrail delivery failures.
- Estimated AWS cost anomaly or Bedrock spend spike.

## Recommended Dashboard

A lightweight CloudWatch dashboard should show:

- API request count, 4xx, 5xx, latency.
- Query Lambda invocations, errors, duration, throttles.
- Cache table read/write capacity usage and throttles.
- Query audit table writes.
- SQS queue depth and DLQ depth.
- Bedrock error count if surfaced through Lambda logs/metrics.

## Current Gaps

- No Terraform-managed CloudWatch alarms.
- No Terraform-managed dashboard.
- No distributed tracing.
- No structured JSON logging library.
- No synthetic canary or deployed end-to-end smoke test.

The current state is enough to inspect runtime behavior manually, but not enough for mature unattended operations.
