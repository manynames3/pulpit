# ADR 006: S3 Vectors and Versioned Retrieval Snapshots

## Status

Accepted, 2026-10-07. Supersedes loading all embeddings into Lambda for normal retrieval.

## Context

The legacy JSON index exceeded the deployed Lambda memory limit. Catalog and
search shared that failure boundary. The archive needs growth without an
always-running search cluster or a full application rewrite.

## Decision

Use S3 Vectors for semantic candidates, sharded lexical postings for bilingual
keyword matching, and per-sermon source chunks in ordinary S3. Publish a small
catalog/manifest pointer after validation. Keep planner, reranker, citations,
authentication, and caching. Provision through a Terraform-managed CloudFormation
stack using the existing AWS provider.

## Consequences

Query memory no longer contains every embedding; catalog is independent of
vector queries. Compatible embeddings need no regeneration. Snapshots support
rollback but need cleanup. Keyword shards and catalog metadata still grow and
need measurement. OpenSearch remains an option for richer integrated search.
