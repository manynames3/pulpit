# Retrieval Quality Iterations

This document summarizes the engineering iterations used to improve Pulpit's sermon search quality. The goal was to make the archive behave less like a literal keyword search and more like a bilingual church librarian: able to understand Korean and English questions, find related sermon evidence, and return cited answers with video sources.

## Starting Point

The first working query path used a serverless RAG pattern:

- Store enriched sermon records and a search index in S3.
- Load the index inside the query Lambda.
- Retrieve relevant sermons with Bedrock Titan embeddings and lexical scoring.
- Generate a cited answer with Bedrock.
- Cache repeat answers in DynamoDB.

That architecture kept idle cost low, but early retrieval quality was inconsistent. It worked for obvious exact keywords, but failed more often when users asked natural-language questions or used English words for Korean sermon content.

## Problems Observed

| User query pattern | Example | Problem |
|---|---|---|
| Natural-language Korean sentence | `최근에 목사님이 자만에 대한 설교를 했나요` | The full sentence could miss results even when the core keyword `자만` existed. |
| English phrase for Korean content | `cloud column` | The archive contained `구름기둥`, but the English phrase did not reliably map to it. |
| English keyword for Korean concept | `money` | The archive contained `돈`, but English-only retrieval returned too few or no results. |
| Related word forms | `fail` vs `failure` | Exact lexical scoring treated related English forms as different terms. |
| Korean suffixes and derived nouns | `고고학`, `고고학자`, `고고학자들` | The archive may contain a derived or plural form rather than the exact query term. |
| Missing source cards | `death` | The answer could cite sermon headings without enough linked source metadata in the rendered result. |

## Iteration 1: Keep the Runtime Serverless

Before improving quality, the system constraint was kept explicit: no always-on search cluster for the current archive size.

The index stays in S3 and query-time ranking stays inside Lambda. This keeps the production demo inexpensive at idle and avoids adding OpenSearch or a third-party vector database before the archive size justifies it.

Tradeoff: retrieval logic has to be carefully engineered in application code.

## Iteration 2: Hybrid Retrieval Instead of Embeddings Only

Pure semantic search was not reliable enough for sermon archive questions because church queries often depend on exact Bible terms, names, Korean phrases, or sermon-specific vocabulary.

The query Lambda now combines:

- Semantic similarity from Titan embeddings when embeddings are available.
- Lexical matching against titles, descriptions, topics, scripture references, and transcript chunks.
- Bible/church term expansion for common Korean and English vocabulary.
- Pastor and admin-configured priority signals, without letting priority override strong relevance.

Result: exact archive language such as `구름기둥`, `돈`, `방주`, or `자만` has a stronger path into the result set.

## Iteration 3: Natural-Language Planning

Users usually ask questions, not clean search terms. The query Lambda now runs a planner step before retrieval.

For each question, the planner extracts:

- question type: simple, detailed, or comparison
- bilingual subqueries
- scripture references
- detected language

Example intent:

```json
{
  "type": "detailed",
  "subqueries": ["recent sermon about pride", "자만과 교만에 대한 설교"],
  "scripture_refs": [],
  "language": "ko"
}
```

Result: a sentence like `최근에 목사님이 자만에 대한 설교를 했나요` can retrieve against the compact concept `자만`, related Korean terms such as `교만`, and an English semantic equivalent.

## Iteration 4: Per-Subquery Retrieval With Union

Instead of relying on one search query, retrieval now runs against multiple subqueries and unions the candidate chunks.

Current behavior:

- Run retrieval for each planner-generated subquery.
- Take the top chunk candidates from each subquery.
- Deduplicate by `chunk_id`.
- Keep the highest score for duplicate chunks.
- Sort the unioned candidate pool by score before collapsing back to sermons.

Result: if one phrasing misses, another phrasing can still surface the right sermon.

## Iteration 5: Larger Candidate Pool Before Final Answer

The system now considers a larger internal candidate pool before producing the final answer.

Current limits:

- `TOP_K = 5`: maximum sermon/source results returned to the user.
- `PER_SUBQUERY_CHUNK_LIMIT = 80`: raw chunk candidates per subquery before diversity trimming.
- `CHUNK_CANDIDATE_LIMIT = 50`: unioned chunk candidates before neighbor expansion and reranking.
- `MATCHED_CHUNKS_PER_SERMON = 3`: excerpt chunks kept per sermon for answer context.
- `ANSWER_CONTEXT_CHAR_LIMIT = 15000`: context size guardrail before calling the answer model.

Result: users still see a concise answer with a small number of sources, but the backend has more evidence to choose from.

## Iteration 6: Neighbor Chunk Expansion

Sometimes the highest-scoring chunk contains the keyword, but the best explanation is immediately before or after it in the transcript.

The query Lambda now expands each selected chunk with nearby chunks from the same sermon only. It does not cross sermon boundaries.

Result: the answer model gets better local context without flooding it with unrelated transcript text.

## Iteration 7: Reranking Evidence Before Answering

After retrieval and neighbor expansion, the system reranks evidence chunks before building the answer context.

This gives the model a more focused evidence set and reduces the chance that a broad but weakly related sermon pushes out a directly relevant one.

Result: the final answer is more likely to cite the sermon excerpts that actually support the user's question.

## Iteration 8: Better English Word Forms

Exact keyword scoring was too brittle for common English forms.

Example:

- `fail`
- `fails`
- `failed`
- `failing`
- `failure`
- `failures`

These now normalize toward the same lexical concept for scoring.

Result: English queries are less sensitive to word form.

## Iteration 9: Korean Morphology-Aware Matching

Korean sermon transcripts often contain suffixes and derived forms. A user may search for `고고학`, but the transcript may say `고고학자` or `고고학자들`.

The lexical layer now considers common Korean forms such as:

- plural/person suffixes: `고고학자`, `고고학자들`
- descriptive forms: `고고학적`
- topic expansions: `발굴`, `유물`, `유적`

The matching is token-aware, not broad substring matching. That avoids accidental matches where a short Korean term appears inside an unrelated word.

Result: related Korean forms can support the same search intent while reducing false positives.

## Iteration 10: Model Selection

The answer-generation model was separated from planning and reranking configuration.

Current model roles:

- Planner model: extracts bilingual retrieval intent.
- Reranker model: scores evidence chunks.
- Answer model: generates the final cited response.
- Embedding model: Titan Embed Text v2 remains the retrieval embedding model.

This allows quality upgrades to the answer model without rebuilding the archive or changing the embedding pipeline.

## Current Behavior

The current query path is:

1. Check DynamoDB cache.
2. Analyze the question into bilingual subqueries.
3. Run per-subquery hybrid retrieval.
4. Union chunk candidates.
5. Prefer literal matches for literal keyword queries.
6. Expand neighboring chunks from the same sermon.
7. Rerank evidence chunks.
8. Collapse chunks back to the top sermons.
9. Build bounded context for Bedrock.
10. Generate a cited answer.
11. Cache and audit-log the response.

## Remaining Limitations

- Some sparse topics still return few results because the underlying archive contains few direct mentions.
- If chunk embeddings are missing from the current index, retrieval leans heavily on lexical scoring until chunk embeddings are backfilled.
- DynamoDB cached answers can hide retrieval improvements until the cache expires or the retrieval version changes.
- The system is still optimized for one church archive, not a general theological corpus.
- A dedicated search backend may become justified if the archive grows enough that Lambda-loaded S3 indexes become too large or slow.

## Next Quality Improvements

The smallest high-impact next steps are:

- Backfill chunk embeddings for every transcript chunk.
- Add a small curated synonym table for high-value Korean/English Bible terms.
- Store query evaluations and mark which returned sermon was actually useful.
- Add admin tools for preferred sermons, hidden sermons, and curated topic mappings.
- Consider OpenSearch Serverless or another managed retrieval layer only when archive size or query volume justifies the extra cost.
