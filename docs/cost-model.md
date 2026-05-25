# Cost Model

Pulpit is designed for low idle cost. This document describes cost drivers and controls without claiming measured production spend.

## Cost Shape

| Component | Cost Behavior | Control |
|---|---|---|
| Cloudflare Pages | Static hosting, low/no idle cost for this use case | No server-side frontend runtime |
| API Gateway | Pay per request | Authenticated endpoints, no polling loop |
| Lambda | Pay per invocation and duration | Serverless runtime, warm index cache, bounded memory |
| DynamoDB | On-demand request pricing and storage | TTL on cache, audit, and eval records |
| S3 | Low-cost object storage and requests | Single index object, lifecycle can be added later |
| Bedrock | Pay per model invocation/tokens | Answer cache, planner/reranker cache, retrieval thresholds |
| CloudTrail | S3 log storage and event delivery costs | Single-region trail, dev teardown |
| GuardDuty | Optional ongoing cost after trial | Disabled in dev, enabled in prod tfvars |
| OpenSearch/vector DB | Not used | Avoids always-on search cost for current archive size |

## Main Cost Controls

- Static frontend avoids app server cost.
- Query path uses Lambda and API Gateway instead of always-on compute.
- DynamoDB tables use pay-per-request mode.
- Search index is stored in S3 instead of a managed search cluster.
- Titan embeddings are generated at ingest/index time, not on every query when avoidable.
- Answer cache uses a 30-day TTL.
- Planner and reranker intermediate outputs use shorter TTLs.
- Cache keys include the S3 index marker, so stale answers are not reused after index changes.
- Local ingestion uses batch caps and sleep intervals to avoid runaway transcript and model calls.
- GuardDuty is optional and disabled in dev.

## Why No Vector Database Yet

The current archive size can fit in a Lambda-friendly S3 index. For this scale, a vector database or OpenSearch Serverless would add fixed cost and operational complexity without enough benefit.

A migration becomes more attractive if:

- the index no longer fits comfortably in Lambda memory
- query latency becomes unacceptable
- concurrent traffic increases materially
- retrieval needs advanced filtering, pagination, or relevance controls that are awkward in code

## Cost Risks

- Bedrock calls are the main variable cost if many cache-missing queries arrive.
- A bad ingestion loop could repeatedly call YouTube, Bedrock metadata extraction, and embedding generation.
- CloudTrail and S3 logs can grow if retained indefinitely.
- Enabling GuardDuty in prod has ongoing cost.
- Adding OpenSearch or a managed vector database would change the idle-cost profile.

## Recommended Next Controls

- Add AWS Budget or Cost Anomaly Detection.
- Add CloudWatch metric filters for cache misses and Bedrock call counts.
- Add S3 lifecycle rules for old CloudTrail logs if retention requirements allow it.
- Add a maximum query size and rate limiting at the edge/API layer.
- Track cost per successful query during a real pilot before changing retrieval architecture.
