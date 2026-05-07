# ADR-002 — Store the search index in S3 instead of using a managed search cluster

**Status:** Accepted

## Context

The archive is still small enough to fit into a Lambda-friendly index file, but the project still needs semantic retrieval, exact-term matching, and low recurring cost. OpenSearch Serverless or an external vector database would solve retrieval, but they add fixed cost and operational weight that the current archive size does not justify.

## Decision

Store enriched sermon JSON in S3 and rebuild a chunked `transcripts/index.json` file. Precompute Titan embeddings at ingest time and let the query Lambda load and rank the S3-backed index directly.

## Consequences

- Zero idle search-cluster cost.
- Retrieval logic stays inside application code and can evolve quickly.
- The query path depends on the index staying reasonably small for Lambda memory and latency limits.
- Future migration to a dedicated search backend is still possible if archive size or traffic grows materially.
