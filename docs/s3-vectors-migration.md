# S3 Vectors Migration

## Why

On 2026-10-07 the deployed 512 MB query Lambda repeatedly timed out loading the
97,659,647-byte legacy JSON index. The archive remained intact: 360 sermons and
8,191 chunks. A local Python JSON load peaked around 914 MB. More memory would
restore headroom temporarily but keep query memory coupled to all embeddings.

## Design

- Ordinary S3 remains the transcript source of truth.
- S3 Vectors stores existing 256-dimension Titan Text Embeddings v2 vectors in a
  cosine-distance index. Migration does not re-embed transcripts.
- Compressed lexical postings live in 256 hash shards. English terms share the
  runtime stemmer; Korean token groups locate candidates, then the existing
  morphology matcher verifies tokens. BM25-style scores use corpus statistics.
- Each snapshot stores compressed, embedding-free source chunks per sermon.
- `indexes/retrieval/manifest.json` contains catalog metadata, vector index ARN,
  corpus statistics, and snapshot ID. Catalog requests read only this file.
- Queries use up to three subqueries, union semantic and lexical candidates,
  diversify to 50 chunks, hydrate selected sermons, expand neighbors, rerank,
  and return at most five cited sermons.
- The compressed-object cache is bounded to 8 MB; selected source chunks are
  expanded in memory. Answer-cache versioning uses the manifest object marker.
- API Gateway still has a 29-second deadline. If it returns 504, the frontend
  waits up to another 30 seconds by calling the same authenticated query route
  with `cacheOnly: true`. Pending responses use HTTP 202. Polling never starts
  another model workflow; the original Lambda can finish and cache its answer.
  This is timeout recovery, not a durable asynchronous job system. If Lambda
  fails or exceeds its 60-second limit, the frontend reports an error.
- If Bedrock reports a transient answer-generation failure, retrieved sources
  remain available with an explicit warning. These partial results cache for
  60 seconds, rather than the normal 30 days; the UI says Sources Available.

S3 Vectors does not provide the lexical layer. Shards and common-term postings
still grow, and catalog metadata currently loads in full. Pagination, posting
segmentation, and relevance evaluation remain future work. This migration does
not establish improved search relevance or unlimited scalability.

## Provision and Publish

The Terraform AWS 5.x provider manages a CloudFormation stack containing
`AWS::S3Vectors::VectorBucket` and `AWS::S3Vectors::Index`; no new provider is
required. The query role has QueryVectors/GetVectors on that index, without
vector writes. Publisher credentials separately require PutVectors/GetVectors,
GetIndex/QueryVectors and writes to the retrieval snapshot prefix and manifest.

For the first migration, provision before enabling the new runtime:

```bash
terraform plan -target=module.query.aws_cloudformation_stack.retrieval \
  -var-file=environments/dev/terraform.tfvars -out=vectors.tfplan
terraform apply vectors.tfplan
python3 scripts/publish_vector_archive.py \
  --bucket pulpit-transcripts-dev-ACCOUNT_ID \
  --index-arn "$(terraform output -raw vector_index_arn)"
./scripts/build-lambda.sh
terraform plan -var-file=environments/dev/terraform.tfvars
```

Review the final plan before applying. Targeting is for initial two-phase
migration only, not routine deployment. The publisher validates embeddings,
uploads vectors, verifies every key and snapshot-filtered search, uploads source
and keyword files, then publishes the active manifest last. Failed imports leave
the previous manifest active. Run one publisher/ingest process at a time;
concurrent publishing is not supported. Interrupted imports can leave orphaned
snapshot data without deleting the original archive.

Normal local ingestion discovers the vector index from the active manifest or
uses `PULPIT_VECTOR_INDEX_ARN`. After rebuilding the legacy export it publishes a
new snapshot. Direct `scripts/rebuild_index.py` runs need the publisher command
afterward. The legacy AWS ingest Lambda does not publish snapshots; use the
local ingestion path.

## Recovery and Cleanup

Restore a previous S3 version of `indexes/retrieval/manifest.json` using
CopyObject. Retained vectors are filtered by that snapshot ID. Answer-cache
markers refresh within 60 seconds; catalog metadata may cache for ten minutes.

For full legacy rollback, deploy a known-good package with
`PULPIT_RETRIEVAL_BACKEND=legacy` and `PULPIT_INDEX_KEY=transcripts/index.json`.
This archive requires more than 512 MB for the legacy path.

CloudFormation Retain policies preserve the vector index and bucket after
Terraform destroy. Delete the index and then the empty vector bucket explicitly
after backup/retention review. Keep previous snapshots while rollback is needed.
Old vectors and versioned snapshot objects accumulate storage charges; no
automatic garbage collector exists yet.

## Verification and Cost

```bash
python3 scripts/test_korean_search.py
python3 scripts/test_vector_archive.py
node scripts/test_frontend.js
terraform fmt -check -recursive
terraform validate
git diff --check
```

Offline tests cover Korean/English terms, snapshot isolation, hidden sermons,
neighbors, invalid embeddings, and failed publication recovery. They do not
measure live relevance. Validate catalog and Korean/English searches after
deploying; compare against golden queries before claiming quality improvements.

No provisioned search cluster is introduced. S3 Vectors bills storage, uploads,
query requests, processing, and billable returned data. Bedrock, Lambda, ordinary
S3, logging, and DynamoDB are separate charges. Reused embeddings avoid migration
embedding calls. Retained snapshots increase storage across revisions.

Sources: [S3 Vectors](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors.html),
[pricing](https://aws.amazon.com/s3/pricing/),
[CloudFormation index](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-s3vectors-index.html).

## Deployment Validation: 2026-10-07

The dev vector snapshot was backfilled from the existing export; all 8,191 keys
were verified. The manifest contains 360 sermons and is 556,527 bytes.
The query Lambda remains at 512 MB. During initial deployed checks, CloudWatch
reported 100-126 MB maximum memory usage instead of the previous 512 MB ceiling.

Final API Gateway integration tests returned HTTP 200 for catalog (360 sermons),
`cloud column` (five sources, 12.7 seconds), and `고고학` (five sources, 13.3 seconds).
Cache-only hits took 26-38 ms of integration time; a pending cache-only query
returned HTTP 202. These are individual smoke observations, not benchmarks or
proof of retrieval quality. API Gateway test-invoke bypasses Cognito; separate
unauthenticated HTTP testing confirmed the public catalog still rejects with 401.

Playwright checked rendered desktop/mobile error display and cache-only recovery
using mocked APIs. No full real-user login/browser session was tested. Bedrock
transient errors occurred during earlier checks; explicit source-only fallback
and its 60-second cache were tested offline, and final model-answer calls passed.

Infrastructure was deployed through reviewed targeted plans for the vector
stack, query IAM, required config/evaluation tables, and query Lambda. A full
plan also revealed existing undeployed admin/ingestion infrastructure changes;
those were not applied as part of this migration. Review them separately before
a future full apply. Original transcript/index objects were retained.
