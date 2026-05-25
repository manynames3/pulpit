# Deployment

Pulpit has two deployment surfaces:

- Cloudflare Pages for the static frontend.
- AWS for identity, API, Lambda, storage, logging, guardrails, and queues.

CI validates and plans, but does not auto-apply infrastructure.

## Environments

Terraform supports `dev` and `prod` through tfvars:

- `environments/dev/terraform.tfvars`
- `environments/prod/terraform.tfvars`

Important differences:

- `enable_guardduty = false` in dev.
- `enable_guardduty = true` in prod.
- Some prod DynamoDB tables enable deletion protection.
- Dev S3 buckets use `force_destroy = true`; prod buckets do not.

## Backend Deployment

Prerequisites:

- Terraform 1.5+
- AWS credentials with permission to manage the resources in `modules/`
- Bedrock model access in `us-east-1`
- A YouTube channel ID

Build packages:

```bash
./scripts/build-lambda.sh
```

Plan:

```bash
terraform init
terraform plan -var-file=environments/dev/terraform.tfvars
```

Apply:

```bash
terraform apply -var-file=environments/dev/terraform.tfvars
```

After first apply, set the real YouTube API key for the AWS-managed ingest path:

```bash
aws ssm put-parameter \
  --name "/pulpit/dev/youtube_api_key" \
  --value "YOUR_API_KEY" \
  --type SecureString \
  --overwrite
```

Do not commit real secrets.

## Frontend Deployment

The current deployed frontend is `frontend-alternative/`.

`wrangler.toml` points Cloudflare Pages to the static output directory. There is no frontend build step.

Manual deploy example:

```bash
npx wrangler pages deploy frontend-alternative --project-name pulpit-archive
```

The older [DEPLOY.md](../DEPLOY.md) includes Cloudflare dashboard setup details and custom-domain notes.

## CI/CD

GitHub Actions workflow: `.github/workflows/ci.yml`.

Validate job:

- checks out the repo
- sets up Python
- sets up Terraform
- builds Lambda packages
- runs local Python regression/syntax checks
- checks frontend inline JavaScript syntax
- runs Terraform fmt and validate
- runs Checkov as soft-fail

Plan job:

- runs after validate
- configures AWS credentials from GitHub secrets
- initializes Terraform with backend
- builds Lambda packages
- runs `terraform plan` against dev tfvars
- comments plan output on trusted pull requests

CI does not run `terraform apply`.

## Deployment Verification

Backend:

```bash
terraform output
aws lambda get-function-configuration --function-name pulpit-query-dev
aws s3api head-object --bucket pulpit-transcripts-dev-ACCOUNT_ID --key transcripts/index.json
```

Frontend:

```bash
curl -L -I https://pulpit.pages.dev
```

Manual product smoke test:

1. Page loads over HTTPS.
2. Signup/login works.
3. Catalog request succeeds after sign-in.
4. Search returns an answer.
5. Source sermon cards render and open YouTube.
6. Browser console has no CORS or auth errors.

## Rollback

AWS:

- Revert the application/infrastructure commit.
- Run `./scripts/build-lambda.sh`.
- Run `terraform plan`.
- Apply the known-good state if the plan is expected.

Frontend:

- Redeploy a previous Cloudflare Pages deployment or a known-good commit.

Search index:

- Restore a previous S3 object version of `transcripts/index.json`.
- The answer cache will miss after the index marker changes.

## Known Deployment Gaps

- CI does not run an end-to-end smoke test against the deployed API.
- CI Checkov is soft-fail rather than blocking.
- CORS should be tightened before a production custom-domain launch.
- Cloudflare Pages deployment is not encoded as Terraform in this repo.
