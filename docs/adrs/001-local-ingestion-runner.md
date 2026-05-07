# ADR-001 — Use a local ingestion runner for YouTube transcripts

**Status:** Accepted

## Context

The project needs sermon transcripts from YouTube. The obvious cloud-native design is an AWS Lambda ingestion job, but YouTube blocks transcript scraping from AWS IP ranges. The official YouTube captions API would avoid that problem, but it requires OAuth 2.0 credentials plus explicit channel-owner consent. An API key alone is not sufficient for caption download methods on account-owned data, and that level of access is not guaranteed for this deployment.

## Decision

Use `scripts/ingest-local.py` as the primary ingestion path. Run it manually or on cron / launchd from a local machine on a residential or church-office internet connection. Use S3 as the handoff point into the rest of the AWS system.

## Consequences

- Ingestion is operationally reliable without adding proxy infrastructure.
- The runtime system still stays cloud-based for search and auth.
- A local machine becomes part of the deployment model.
- If the church later grants OAuth 2.0 access to the channel owner account, the ingest path should be reconsidered in favor of the official captions API.
- The repo retains an AWS ingestion Lambda path, but it is not the preferred production path.
