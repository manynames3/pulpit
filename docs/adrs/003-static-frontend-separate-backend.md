# ADR-003 — Serve the frontend as a static site and keep the backend in AWS

**Status:** Accepted

## Context

The deployed frontend is plain HTML, CSS, and JavaScript. It does not require server-side rendering, a Node runtime, or a build pipeline. The backend already lives in AWS because it depends on Cognito, API Gateway, Lambda, DynamoDB, S3, and Bedrock.

## Decision

Deploy `frontend-alternative/` as a static site on Cloudflare Pages and keep the authenticated query backend in AWS.

## Consequences

- Frontend hosting stays inexpensive and operationally simple.
- AWS remains the system of record for identity, AI, storage, and logging.
- CORS must be configured correctly between the frontend origin and API Gateway.
- The repo preserves both the original prototype frontend and the deployed frontend.
