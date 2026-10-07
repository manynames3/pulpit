# Testing and Validation

This repo has lightweight automated validation focused on backend retrieval behavior, snapshot publication, cache handling, frontend failure recovery, packaging, and Terraform. It does not yet have full unit-test coverage or automated real-user end-to-end browser coverage.

## Local Checks

Run from the repo root:

```bash
python3 -m pip install -r lambda/query/requirements.txt
python3 scripts/test_korean_search.py
python3 scripts/test_vector_archive.py
python3 -m py_compile lambda/query/query_service.py scripts/rebuild_index.py scripts/evaluate_retrieval.py scripts/test_korean_search.py
node scripts/test_frontend.js
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
./scripts/build-lambda.sh
git diff --check
```

## Retrieval Regression Tests

File: `scripts/test_korean_search.py`

Coverage includes:

- Korean lexical suffix handling.
- Bilingual query expansion.
- Packaged synonym config loading.
- BM25-style chunk scoring.
- Source snippet bounds.
- Answer-cache key sensitivity to S3 index markers.
- Warm index cache clearing when the index marker changes.
- Archive stats keep `key_themes` distinct from `topics`.
- Chunk diversity for broad keyword searches.

These tests run without live AWS calls by setting test environment variables and patching selected functions.

## Vector Archive and Cache Tests

File: `scripts/test_vector_archive.py`, also run in CI.

Ten offline tests cover snapshot isolation, metadata-only catalog reads,
Korean/English postings, source hydration and neighbors, invalid embeddings,
failed publication recovery, DynamoDB score serialization, rerank candidate
validation, cache-only polling, and transient answer-model fallback/expiry.
They do not measure live search relevance. Dated deployment observations and
their limitations are recorded in the [migration guide](s3-vectors-migration.md#deployment-validation-2026-10-07).

## Retrieval Evaluation Harness

Files:

- `scripts/evaluate_retrieval.py`
- `eval/retrieval-golden.json`

Usage:

```bash
python3 scripts/evaluate_retrieval.py --index /path/to/transcripts/index.json
```

Default mode disables live Bedrock embeddings so it can run locally against an exported index. `--use-bedrock` enables fuller scoring when AWS credentials and Bedrock access are available.

The current golden set is intentionally small. It is useful as a relevance smoke test, not a statistically meaningful benchmark.

## Terraform Validation

CI and local checks run:

- `terraform fmt -check -recursive`
- `terraform init -backend=false`
- `terraform validate`
- Checkov for Terraform security visibility

Checkov is currently soft-fail in CI, so it does not block merges.

## Lambda Packaging Validation

`./scripts/build-lambda.sh` installs Lambda dependencies into package directories and copies Python/JSON source files. Terraform zips those package directories using `archive_file`.

The build currently packages:

- `lambda/ingest`
- `lambda/query`
- `lambda/admin-trigger`

## Frontend Validation

The deployed frontend is static HTML/CSS/JavaScript. There is no frontend build pipeline. The CI check validates inline script syntax and executes the actual async handlers in a Node.js VM with mocked DOM/API responses:

```bash
node scripts/test_frontend.js
```

It checks catalog/query errors, gateway timeout recovery through cache-only
polls, and the Sources Available state. This is not a rendered browser or
Cognito login test. One-off Playwright checks used mocked APIs, not a real-user
session; see the migration guide for deployment validation scope.

Manual smoke:

```bash
python3 -m http.server 8767 --directory frontend-alternative
```

Then open `http://127.0.0.1:8767/`.

## CI Workflow

File: `.github/workflows/ci.yml`

Validate job:

- build Lambda packages
- run Python retrieval, vector archive, publication, and cache tests
- run Python compile checks
- run frontend JS syntax and mocked failure/recovery checks
- run Terraform fmt/validate
- run Checkov

Plan job:

- runs after validation
- uses AWS credentials from GitHub secrets
- runs Terraform plan against dev tfvars
- comments plan output on trusted PRs

## Known Gaps

- No pytest suite or coverage report.
- Frontend tests use a mocked DOM, not a real browser or complete UI coverage.
- No Playwright/Cypress deployed smoke test.
- No mocked API integration test around API Gateway event shapes.
- No load test or latency budget.
- No automated restore/rollback drill.

## Next Testing Improvements

- Convert the retrieval regression script to pytest while keeping no-AWS defaults.
- Add API handler tests for `/query`, `/catalog`, auth claims, and error paths.
- Add a browser smoke test for login, catalog, query, and source-card rendering.
- Add a markdown/link check for docs.
- Add a Terraform plan policy gate after triaging Checkov output.
