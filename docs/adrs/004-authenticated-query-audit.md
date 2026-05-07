# ADR-004 — Enforce authenticated access and keep an audit log of queries

**Status:** Accepted

## Context

The archive is intended for a real church context, not anonymous public search. The system also needs a record of what users asked and what answers were returned for accountability and review.

## Decision

Use Cognito user pools and groups for authentication, enforce access at API Gateway, cache repeated answers in one DynamoDB table, and write user question / answer records to a separate DynamoDB audit-log table.

## Consequences

- The system supports member/staff access control without custom auth code.
- Repeated questions are cheaper and faster.
- Query activity becomes reviewable.
- The product has more sign-in friction than a public unauthenticated demo.
