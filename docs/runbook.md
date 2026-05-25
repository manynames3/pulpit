# Runbook

This runbook covers the operational paths that exist in this repo. It does not assume production traffic, an on-call rotation, or custom monitoring that has not been implemented.

## Normal Deployment Checks

Before applying infrastructure changes:

```bash
./scripts/build-lambda.sh
terraform fmt -check -recursive
terraform init
terraform validate
terraform plan -var-file=environments/dev/terraform.tfvars
```

Before pushing code changes:

```bash
python3 scripts/test_korean_search.py
python3 -m py_compile lambda/query/query_service.py scripts/rebuild_index.py scripts/evaluate_retrieval.py scripts/test_korean_search.py
awk '/<script>/{flag=1; next} /<\\/script>/{flag=0} flag' frontend-alternative/index.html | node --check
git diff --check
```

## Query API Troubleshooting

Symptoms:

- Frontend search fails.
- Browser shows an auth or CORS error.
- API Gateway returns 4xx/5xx.
- Answers are missing sources or stale content.

Checks:

1. Confirm the user can sign in through Cognito.
2. Inspect browser console errors from the frontend.
3. Check API Gateway and query Lambda CloudWatch logs.
4. Confirm `transcripts/index.json` exists in the transcript bucket.
5. Confirm the query Lambda environment variables match the Terraform outputs.
6. Check DynamoDB cache behavior if stale answers are suspected.

Useful AWS checks:

```bash
aws lambda get-function-configuration --function-name pulpit-query-dev
aws s3api head-object --bucket pulpit-transcripts-dev-ACCOUNT_ID --key transcripts/index.json
aws dynamodb describe-table --table-name pulpit-cache-dev
aws dynamodb describe-table --table-name pulpit-queries-dev
```

Expected cache behavior:

- Answers are cached in DynamoDB with TTL.
- Cache keys include retrieval version, retrieval config version, synonym version, preferred language, and the current S3 index marker.
- Updating `transcripts/index.json` should cause answer-cache misses for future queries without manually clearing the table.

## Catalog Troubleshooting

Symptoms:

- Indexed archive list is empty.
- Top topics or lessons are empty.
- Frontend shows loading text after the catalog response has completed.

Checks:

1. Verify `GET /catalog` returns HTTP 200 with a Cognito token.
2. Verify `transcripts/index.json` includes `sermons`.
3. Check whether sermon entries have `topics` or `key_themes`.
4. Rebuild the index if transcript JSON exists but the index is stale.

The frontend has fallbacks for missing `key_themes`, but if both `topics` and `key_themes` are absent, it should show an honest empty state.

## Ingestion Troubleshooting

Primary path:

```bash
./scripts/run-ingest-batch.sh backlog
```

Common failures:

| Symptom | Likely Cause | Response |
|---|---|---|
| YouTube IP block | Too many transcript attempts or blocked network | Stop the run, wait, lower batch limits |
| Many subtitle failures | Videos have disabled or missing captions | Let skip markers prevent repeated retries |
| Missing metadata | Bedrock call failed or local config missing | Check AWS credentials and Bedrock model access |
| Stale search results | Index was not rebuilt or uploaded | Run `scripts/rebuild_index.py` through the batch wrapper |

Cost and block controls:

- `PULPIT_MAX_NEW_PER_RUN`
- `PULPIT_MAX_TRANSCRIPT_ATTEMPTS`
- `PULPIT_SLEEP_SEC`
- `PULPIT_CONSECUTIVE_EXIST_STOP`

## Admin Ingest Trigger

The admin route is `POST /admin/ingest/run`.

Controls:

- API Gateway requires Cognito.
- `lambda/admin-trigger/handler.py` checks Cognito groups against `ADMIN_GROUPS`.
- The function sends an SQS message instead of giving the browser AWS permissions.
- The ingest queue has a DLQ.

Troubleshooting:

```bash
aws sqs get-queue-attributes \
  --queue-url QUEUE_URL \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
```

Check the DLQ if ingestion jobs disappear without successful output.

## Rollback and Recovery

Application code:

- Revert the commit and rebuild Lambda packages.
- Run `terraform plan` and `terraform apply` explicitly.

Frontend:

- Redeploy a previous Cloudflare Pages deployment from the Cloudflare dashboard or redeploy a known-good commit.

Search index:

- S3 versioning is enabled on the transcript bucket.
- Restore a previous version of `transcripts/index.json` if a bad index was uploaded.
- After restoring the object, the S3 index marker changes and future answer-cache keys will miss.

Audit/cache data:

- DynamoDB TTL handles old audit/cache records.
- Do not delete audit tables casually; prod deletion protection is enabled for selected tables.

## Teardown

Use [teardown.md](teardown.md). Do not run destroy in prod without exporting transcripts, CloudTrail logs, and query audit data that must be retained.

## Current Operational Gaps

- No custom CloudWatch alarms are codified.
- No dashboard is codified.
- No automated end-to-end deployed smoke test exists.
- No formal incident severity model or paging policy exists.
