# ADRs

This directory records the main architecture decisions for Pulpit.

- [ADR-001 — Use a local ingestion runner for YouTube transcripts](001-local-ingestion-runner.md)
- [ADR-002 — Store the search index in S3 instead of using a managed search cluster](002-s3-backed-search-index.md)
- [ADR-003 — Serve the frontend as a static site and keep the backend in AWS](003-static-frontend-separate-backend.md)
- [ADR-004 — Enforce authenticated access and keep an audit log of queries](004-authenticated-query-audit.md)
- [ADR-005 — Store AWS-managed secrets in SSM Parameter Store](005-ssm-secret-management.md)
