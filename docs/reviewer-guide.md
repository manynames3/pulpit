# Reviewer Guide

This guide is for a busy recruiter or hiring manager who wants to inspect the repo quickly and understand what it proves.

## What to Look at First

1. [README](../README.md) for the 60-second project summary and evidence matrix.
2. [Architecture](architecture.md) for system shape, runtime flow, and service boundaries.
3. [Query service](../lambda/query/query_service.py) for retrieval, caching, Bedrock calls, audit logging, and catalog behavior.
4. [Terraform modules](../modules/) for infrastructure, IAM, auth, storage, API, and security resources.
5. [CI workflow](../.github/workflows/ci.yml) for validation and deployment discipline.
6. [ADRs](adrs/README.md) for architecture decisions and tradeoffs.

## What This Project Proves

- Ability to design a production-style serverless AWS application.
- Practical RAG implementation without assuming a managed vector database is always the right answer.
- Cost-aware architecture choices for low-traffic or nonprofit use cases.
- Infrastructure-as-code ownership with Terraform modules and environment tfvars.
- Authenticated API design with Cognito and group-aware admin workflows.
- Basic security posture: scoped IAM, SSM SecureString, S3 public access blocks, CloudTrail, guardrails.
- Operational thinking: CI checks, runbook, teardown, known limitations, and recovery notes.
- Retrieval quality iteration with tests and documented evaluation strategy.

## File Map

| Area | Files |
|---|---|
| Infrastructure | `main.tf`, `variables.tf`, `outputs.tf`, `versions.tf`, `modules/` |
| Query backend | `lambda/query/handler.py`, `lambda/query/query_service.py`, `lambda/query/retrieval_synonyms.json` |
| Admin trigger | `lambda/admin-trigger/handler.py`, `modules/query/api-gateway.tf`, `modules/query/lambda.tf` |
| Ingestion | `scripts/ingest-local.py`, `scripts/rebuild_index.py`, `lambda/ingest/handler.py`, `modules/ingestion/` |
| Frontend | `frontend-alternative/index.html`, `wrangler.toml` |
| Tests/eval | `scripts/test_korean_search.py`, `scripts/evaluate_retrieval.py`, `eval/retrieval-golden.json` |
| CI | `.github/workflows/ci.yml` |
| Operations | `docs/runbook.md`, `docs/deployment.md`, `docs/teardown.md`, `DEPLOY.md` |
| Security | `docs/security.md`, `modules/security/`, `modules/query/guardrails.tf` |
| Tradeoffs | `docs/tradeoffs.md`, `docs/adrs/` |

## How to Run or Inspect It

Read-only local validation:

```bash
python3 scripts/test_korean_search.py
python3 -m py_compile lambda/query/query_service.py scripts/rebuild_index.py scripts/evaluate_retrieval.py scripts/test_korean_search.py
awk '/<script>/{flag=1; next} /<\\/script>/{flag=0} flag' frontend-alternative/index.html | node --check
terraform init -backend=false
terraform fmt -check -recursive
terraform validate
```

Build Lambda packages:

```bash
./scripts/build-lambda.sh
```

Inspect infrastructure plan when AWS credentials are available:

```bash
terraform init
terraform plan -var-file=environments/dev/terraform.tfvars
```

Serve the frontend locally:

```bash
python3 -m http.server 8767 --directory frontend-alternative
```

## Strongest Engineering Decisions

- Used S3 plus Lambda ranking for the current archive size instead of adding a fixed-cost search cluster.
- Made answer-cache keys depend on the current S3 index marker so new sermon data invalidates cached answers automatically.
- Kept static frontend hosting separate from the AWS backend.
- Used Cognito at API Gateway and group checks inside the admin trigger Lambda instead of exposing direct AWS credentials to the browser.
- Documented the local ingestion runner as an explicit operational boundary rather than pretending ingestion is fully cloud-native.
- Kept Terraform apply manual; CI validates and plans but does not auto-deploy infrastructure.

## Tradeoffs Made

- S3 index search is cheaper and simpler than a vector database, but it will need replacement if archive size or latency requirements grow.
- Local ingestion is reliable for the current YouTube constraint, but it adds an operator-owned machine to the system.
- Broad CORS is convenient for early deployment, but production should restrict origins.
- CloudTrail and logs exist, but custom alarms and dashboards are still a next step.
- The frontend is static and simple, but there is no frontend test framework.

## Demo-Only or Incomplete Areas

- The live frontend requires Cognito access to search the private archive.
- The AWS ingest Lambda path is not the preferred ingestion route because of YouTube cloud-IP blocking.
- `modules/knowledge-base` is retained as a previous/experimental path and is not active in `main.tf`.
- Checkov is currently soft-fail in CI, useful for visibility but not a hard gate.
- Custom CloudWatch alarms, dashboards, WAF, and restore drills are not implemented yet.

## What I Would Improve Next

- Restrict API CORS to the real Cloudflare/custom domain.
- Add CloudWatch alarms for Lambda errors, API 5xx, DLQ depth, cache failures, and cost anomalies.
- Add a deployed smoke test that covers login, catalog, query, and source rendering.
- Move ingestion to the official YouTube captions API if owner-authorized OAuth is approved.
- Add a documented restore drill for rolling back `transcripts/index.json` from S3 version history.
