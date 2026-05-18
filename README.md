# Pulpit
### Serverless bilingual sermon search built with AWS Bedrock, Lambda, Cognito, DynamoDB, S3, Terraform, and Cloudflare Pages

[![Pulpit CI](https://github.com/manynames3/pulpit/actions/workflows/ci.yml/badge.svg)](https://github.com/manynames3/pulpit/actions/workflows/ci.yml)

Pulpit is a production-style retrieval application for searching a Korean-English sermon archive with natural language. It ingests YouTube captions, builds a chunked hybrid search index, and serves authenticated cited answers through a low-idle-cost serverless backend.

**Live demo:** [https://pulpit.pages.dev](https://pulpit.pages.dev)

## Key Outcomes

- Built a production-style bilingual sermon search app with authenticated query, cited answers, and a live deployed frontend.
- Kept runtime cost low by using a static frontend, serverless AWS backend, and an S3-backed hybrid search index instead of always-on search infrastructure.
- Solved a real ingest constraint by moving transcript collection to a local runner when YouTube blocked cloud IP ranges.

## What I built

- Static frontend deployed on Cloudflare Pages
- AWS query backend using API Gateway, Lambda, Cognito, DynamoDB, S3, and Bedrock
- Local ingestion and indexing pipeline for YouTube captions
- Terraform infrastructure and GitHub Actions validation pipeline

**Core stack:** AWS Bedrock · Lambda · Cognito · DynamoDB · S3 · Terraform · Cloudflare Pages · Python · JavaScript

**Interesting constraint:** transcript ingestion runs locally instead of in AWS because YouTube blocks cloud IP ranges for the caption retrieval path used by this project.

## About

- Built for Atlanta Bethel Church, a Korean-English bilingual congregation.
- Solves a practical archive problem: “Has Pastor preached on this topic, passage, or question?”
- Uses a low-idle-cost runtime model: static frontend + serverless query backend + local ingestion runner.
- Deploys infrastructure with Terraform and validates it in GitHub Actions.

## Tech Stack

### Application

- Static HTML/CSS/JavaScript frontend
- `frontend-alternative/` for the current deployed UI
- `frontend/` for the original terminal-style prototype

### AWS

- API Gateway (REST API)
- Cognito User Pools and groups
- Lambda (Python 3.12)
- DynamoDB
- S3
- CloudTrail
- Bedrock Guardrails
- Bedrock Claude Haiku 4.5 for answer generation
- Bedrock Nova Lite for ingestion metadata extraction
- Titan Embed Text v2

### Ingestion and indexing

- YouTube Data API v3
- `youtube-transcript-api`
- Local cron-friendly Python ingestion runner
- S3-backed chunked search index (`transcripts/index.json`)

### Infrastructure and CI

- Terraform
- GitHub Actions
- Checkov

## Engineering Highlights

- **Chunked hybrid retrieval without a vector database.** Search runs from a prebuilt S3 index plus Lambda ranking logic, which avoids OpenSearch or Pinecone idle cost.
- **Low-cost architecture by design.** The system pushes expensive work to ingest time and keeps query-time infrastructure serverless.
- **Pragmatic ingestion workaround.** The production ingestion path runs locally on a residential IP because YouTube blocks cloud IPs for transcript scraping.
- **Authenticated and auditable query flow.** Cognito protects the API, DynamoDB caches repeat questions, and query logs are stored for accountability.
- **Two frontend tracks in one repo.** The original terminal UI is preserved, while the deployed member-facing frontend lives in `frontend-alternative/`.

## Architecture

The system has three distinct planes:

1. **Frontend delivery**
   - `frontend-alternative/` is deployed as a static site on Cloudflare Pages.
2. **Query runtime**
   - AWS handles auth, API, retrieval, answer generation, caching, and audit logging.
3. **Ingestion and indexing**
   - A local script pulls YouTube captions, enriches sermons with Bedrock, and rebuilds the S3 search index.

Detailed architecture documentation:

- [Architecture overview](docs/architecture.md)
- [Architecture decision records](docs/adrs/README.md)

## Runtime Data on AWS

Transcript data and runtime state are visible in AWS:

- `pulpit-transcripts-dev-636305658578/transcripts/<year>/...` stores sermon JSON records.
- `pulpit-transcripts-dev-636305658578/transcripts/index.json` stores the search index used by the query Lambda.
- `pulpit-cache-dev` stores cached answers keyed by question hash, language, and retrieval version.
- `pulpit-queries-dev` stores audit log entries for actual user queries and responses.
- `pulpit-cloudtrail-dev-636305658578/AWSLogs/...` stores AWS activity logs.

![S3 transcript files](docs/screenshots/aws-s3-transcripts-2026.png)

![DynamoDB cache table](docs/screenshots/aws-dynamodb-cache-table.png)

![DynamoDB query log](docs/screenshots/aws-dynamodb-query-log.png)

![CloudTrail S3 bucket](docs/screenshots/aws-s3-cloudtrail-logs.png)

## Why this exists

Uploading sermon notes or transcripts into a general chat product works for one person once. It does not create a durable, shared, auditable archive.

| Concern | One-off chat upload | Pulpit |
|---|---|---|
| Shared access | Per-user | Church-wide authenticated access |
| Persistence | Re-upload every session | Archive stays in AWS |
| Search scope | Session-bound | Whole indexed archive |
| Auditability | None | Query log in DynamoDB |
| Access control | None | Cognito user groups |
| Cost model | Per-seat or ad hoc | Shared infrastructure cost |

## Project Structure

```text
pulpit/
├── frontend/                    # Original terminal-style prototype UI
├── frontend-alternative/        # Current deployed static frontend
├── lambda/
│   ├── ingest/                  # Legacy AWS-based ingestion path
│   └── query/                   # Query API + catalog endpoint
├── modules/
│   ├── ingestion/               # S3, EventBridge, ingest Lambda, SSM
│   ├── query/                   # API Gateway, Cognito, DynamoDB, guardrails, query Lambda
│   ├── security/                # CloudTrail and optional GuardDuty
│   └── knowledge-base/          # Previous / experimental KB path, not active in main.tf
├── environments/
│   ├── dev/
│   └── prod/
├── scripts/
│   ├── ingest-local.py
│   ├── rebuild_index.py
│   ├── run-ingest-batch.sh
│   ├── install_ingest_cron.sh
│   └── build-lambda.sh
├── docs/
│   ├── architecture.md
│   ├── adrs/
│   └── screenshots/
├── .github/workflows/ci.yml
├── DEPLOY.md
├── main.tf
├── variables.tf
├── outputs.tf
└── wrangler.toml
```

## Deployment

### Frontend

- Live demo is served from Cloudflare Pages.
- `wrangler.toml` points Pages at `frontend-alternative/`.
- Details are in [DEPLOY.md](DEPLOY.md).

### AWS backend

Terraform provisions:

- S3 transcript bucket
- API Gateway REST API
- Cognito user pool and groups
- Query Lambda
- DynamoDB cache and query log tables
- CloudTrail
- optional GuardDuty

Typical flow:

```bash
terraform init
terraform plan -var-file=environments/dev/terraform.tfvars
terraform apply -var-file=environments/dev/terraform.tfvars
```

## Running Ingestion

The reliable ingestion path is local, not cloud-hosted.

Why:

- YouTube blocks transcript scraping from AWS IP ranges.
- A local machine on a residential or church-office connection works reliably enough to backfill in small batches.
- A better option would be the official YouTube captions API, but that requires OAuth 2.0 credentials and explicit consent from the channel owner. An API key alone is not enough to access caption download methods for account-owned data.

If the church grants OAuth 2.0 access to the channel owner account, the ingestion path should move to the official captions API before adding more scraping workarounds.

Current runner files:

- `scripts/ingest-local.py`
- `scripts/run-ingest-batch.sh`
- `scripts/install_ingest_cron.sh`
- `scripts/pulpit-ingest.env.example`

Example setup:

```bash
brew install yt-dlp ffmpeg
mkdir -p ~/.config
cp scripts/pulpit-ingest.env.example ~/.config/pulpit-ingest.env
# edit ~/.config/pulpit-ingest.env

./scripts/run-ingest-batch.sh backlog
./scripts/install_ingest_cron.sh backlog "*/30 * * * *"
```

The ingestion script:

- fetches YouTube uploads
- filters out non-sermon / non-lead-pastor content
- downloads transcript text
- extracts metadata with Bedrock
- generates Titan embeddings
- uploads sermon JSON to S3
- rebuilds `transcripts/index.json`

## Security and Privacy

- Cognito protects query and catalog endpoints.
- API Gateway routes all authenticated traffic through the query Lambda.
- Bedrock Guardrails block off-topic or unsafe prompt paths.
- DynamoDB query logs provide an audit trail for staff review.
- CloudTrail captures AWS API activity.
- AWS-managed secrets live in SSM Parameter Store for cloud-side ingestion resources.

## Limitations

- **YouTube transcript retrieval is the hard constraint.** The local runner exists because the caption-scraping path is unreliable from cloud IPs.
- **Official YouTube caption access depends on account-owner consent.** A more robust ingestion path is available through the YouTube Data API, but only if the channel owner grants OAuth 2.0 access; an API key alone is insufficient.
- **Transcript quality depends on YouTube captions.** If a video has bad captions or no captions, retrieval quality drops.
- **The live demo is church-specific.** Prompting, content, and guardrails are tuned for one sermon archive, not for general-purpose document search.
- **The repo carries both active and legacy paths.** `main.tf` uses the low-cost local-ingest architecture, while some legacy AWS ingest resources remain in the repo for reference.

## CI/CD and Validation

Every push runs:

- `terraform fmt -check -recursive`
- `terraform init -backend=false`
- `terraform validate`
- Checkov
- `terraform plan` against the dev environment for trusted pushes / PRs

CI does not auto-apply Terraform.

## Additional Docs

- [Architecture overview](docs/architecture.md)
- [ADRs](docs/adrs/README.md)
- [Deployment guide](DEPLOY.md)

## License

MIT
