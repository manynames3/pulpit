# Teardown

This document describes how to remove or clean up Pulpit resources safely. It is especially important because transcript data, audit logs, and CloudTrail logs may need retention before infrastructure is destroyed.

## Before Destroying

Confirm the environment:

```bash
terraform workspace show
terraform plan -destroy -var-file=environments/dev/terraform.tfvars
```

Export anything that must be retained:

- transcript JSON under `transcripts/`
- `transcripts/index.json`
- DynamoDB query audit records
- CloudTrail logs
- retrieval eval samples if useful
- Cloudflare Pages deployment settings if you need to recreate them

## Dev Teardown

Dev is configured to be easier to destroy:

- transcript bucket uses `force_destroy = true`
- CloudTrail log bucket uses `force_destroy = true`
- selected DynamoDB deletion protection is off
- GuardDuty is disabled by default

Destroy:

```bash
terraform destroy -var-file=environments/dev/terraform.tfvars
```

Then remove local-only files if appropriate:

```bash
rm -f .env
rm -f terraform.tfstate terraform.tfstate.backup
rm -rf .terraform
```

Do not delete local files if you still need the state for recovery or auditing.

## Prod Teardown

Prod is intentionally harder to destroy:

- transcript bucket does not force-destroy objects
- CloudTrail log bucket does not force-destroy objects
- selected DynamoDB tables enable deletion protection
- GuardDuty is enabled in prod tfvars

Recommended sequence:

1. Export retained data.
2. Disable scheduled ingestion.
3. Confirm no active users need the archive.
4. Remove or archive Cloudflare Pages frontend.
5. Decide whether audit logs must be retained.
6. Disable deletion protection only after data-retention approval.
7. Run `terraform plan -destroy`.
8. Run `terraform destroy` only after the plan is reviewed.

## S3 Index Rollback Instead of Teardown

If the problem is bad retrieval data, do not destroy infrastructure. Restore an earlier S3 object version:

```bash
aws s3api list-object-versions \
  --bucket BUCKET_NAME \
  --prefix transcripts/index.json

aws s3api copy-object \
  --bucket BUCKET_NAME \
  --copy-source BUCKET_NAME/transcripts/index.json?versionId=VERSION_ID \
  --key transcripts/index.json
```

When `transcripts/index.json` changes, the query Lambda's index marker and answer-cache keys change automatically.

## Local Ingestion Cleanup

If a local ingestion machine is being retired:

- remove `~/.config/pulpit-ingest.env`
- remove cron/launchd entries created by `scripts/install_ingest_cron.sh`
- remove local logs containing transcript or error output if not needed
- revoke or rotate the YouTube API key if it was stored on that machine

## Remaining Gaps

- No automated retention/export workflow exists.
- No formal backup policy is encoded in Terraform beyond S3 versioning and DynamoDB TTL/deletion-protection choices.
- No restore drill has been recorded.
