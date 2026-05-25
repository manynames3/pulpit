# Architecture Decision Records

This directory captures the main architecture decisions behind Pulpit. Each ADR is intentionally short: title, status, context, decision, and consequences.

## Current ADRs

| ADR | Decision | Why It Matters |
|---|---|---|
| [ADR-001](001-local-ingestion-runner.md) | Use a local ingestion runner for YouTube transcripts | Documents the YouTube cloud-IP constraint and the operational workaround |
| [ADR-002](002-s3-backed-search-index.md) | Store the search index in S3 instead of using a managed search cluster | Shows the cost and scaling tradeoff behind the current retrieval architecture |
| [ADR-003](003-static-frontend-separate-backend.md) | Serve the frontend as a static site and keep the backend in AWS | Explains the frontend/backend deployment split |
| [ADR-004](004-authenticated-query-audit.md) | Enforce authenticated access and keep an audit log of queries | Captures auth, accountability, and cache/audit table choices |
| [ADR-005](005-ssm-secret-management.md) | Store AWS-managed secrets in SSM Parameter Store | Records the secrets-management boundary for cloud resources |

## How to Read These

Start with ADR-001 and ADR-002. Those two decisions explain the most important shape of the system:

- ingestion is local because the cloud path is blocked by YouTube behavior
- retrieval is S3-backed because the current archive does not justify always-on search infrastructure

The remaining ADRs explain deployment, access control, auditability, and secrets handling.
