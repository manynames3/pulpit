# Tradeoffs

This project is intentionally pragmatic. The goal was a credible, low-cost sermon archive system, not a maximal cloud reference architecture.

## S3 Vectors and Custom Lexical Search

Decision:

- Store transcripts, compressed lexical postings, and source chunks in S3.
- Retrieve semantic candidates through S3 Vectors and hydrate selected sermons.

Why:

- The full JSON index exceeded the deployed Lambda memory limit.
- S3 Vectors avoids provisioned search compute while decoupling embeddings from query memory.
- Retrieval logic can be tuned directly in Python.

Cost:

- Lexical posting shards and catalog metadata still grow with the archive.
- Retained snapshots require explicit cleanup.
- Search operations are application code rather than a managed search query language.

## Local Ingestion Instead of Fully Cloud Ingestion

Decision:

- Use `scripts/ingest-local.py` as the reliable primary ingestion path.

Why:

- YouTube blocks transcript scraping from AWS IP ranges.
- The official caption download API requires channel-owner OAuth 2.0 consent.
- A local runner is simpler than proxy infrastructure.

Cost:

- A local machine becomes part of the operational model.
- Scheduling, logs, and secrets have to be managed on that machine.
- The cloud ingestion Lambda remains secondary/legacy until the official API path is available.

## Static Frontend Instead of Full Web App Framework

Decision:

- Use plain static HTML/CSS/JavaScript on Cloudflare Pages.

Why:

- The UI does not need server-side rendering or a backend-for-frontend.
- Static hosting keeps cost and deployment complexity low.
- Cognito and API Gateway already handle backend needs.

Cost:

- No frontend component test setup.
- Manual state management in one HTML file can become unwieldy.
- Future frontend growth may justify a framework.

## Cognito and API Gateway Instead of Custom Auth

Decision:

- Use Cognito user pools and API Gateway Cognito authorizers.

Why:

- Avoids writing password/auth token handling.
- Integrates directly with API Gateway.
- Group claims can support staff/admin paths.

Cost:

- Cognito UX and configuration have sharp edges.
- Local development requires either real Cognito config or mocked flows.

## DynamoDB Cache and Audit Tables

Decision:

- Use DynamoDB pay-per-request tables for answer cache, query audit logs, admin config, and retrieval eval samples.

Why:

- Serverless, low maintenance, TTL support.
- Cache and audit data have simple access patterns.

Cost:

- Querying audit data for analytics is limited without exports or secondary indexes.
- TTL is not immediate deletion.

## Guardrails Plus Retrieval Constraints

Decision:

- Use Bedrock Guardrails and prompt/retrieval constraints.

Why:

- The app should answer from sermon content, not act as a general advice bot.
- Guardrails provide an API-level control that prompt instructions alone cannot.

Cost:

- Guardrails are not a complete safety system.
- Content boundaries still need human review and product judgment.

## Current Hardening Tradeoffs

Known choices made for speed or simplicity:

- CORS is broad and should be restricted before a custom-domain production launch.
- Checkov is soft-fail in CI while findings are reviewed.
- No WAF is configured.
- No custom CloudWatch alarms are codified.
- No customer-managed KMS key is used for S3.

These are documented gaps rather than hidden assumptions.
