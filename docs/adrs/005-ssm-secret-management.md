# ADR-005 — Store AWS-managed secrets in SSM Parameter Store

**Status:** Accepted

## Context

The cloud-side ingestion and backend resources need secrets such as the YouTube API key. Putting those values in Terraform variables, committed files, or Lambda environment variables creates unnecessary exposure risk.

## Decision

Provision an SSM SecureString parameter path in Terraform and have AWS-managed components read the real secret value at runtime. Keep the local ingestion runner separate, with its own machine-local environment file.

## Consequences

- Secrets used by AWS-managed components stay out of git and out of Terraform state changes after bootstrap.
- The local ingestion runner still needs its own machine-local configuration.
- Secret rotation is straightforward on the AWS side.
