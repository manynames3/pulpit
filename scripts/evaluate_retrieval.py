#!/usr/bin/env python3
"""Evaluate Pulpit retrieval against a small golden query set.

Default mode is lexical-only so it can run without AWS calls:

    python3 scripts/evaluate_retrieval.py --index /path/to/index.json

Use --use-bedrock to keep query embeddings enabled for fuller local scoring.
"""

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUERY_SRC = ROOT / "lambda" / "query"
DEFAULT_GOLDEN = ROOT / "eval" / "retrieval-golden.json"

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("BEDROCK_MODEL_PLANNER", "amazon.nova-lite-v1:0")
os.environ.setdefault("BEDROCK_MODEL_RERANKER", "amazon.nova-lite-v1:0")
os.environ.setdefault("BEDROCK_MODEL_ANSWER", "amazon.nova-lite-v1:0")
os.environ.setdefault("TRANSCRIPT_BUCKET", "test-bucket")
os.environ.setdefault("GUARDRAIL_ID", "test-guardrail")
os.environ.setdefault("GUARDRAIL_VERSION", "1")
os.environ.setdefault("DYNAMODB_TABLE", "test-query-log")
os.environ.setdefault("CACHE_TABLE", "test-cache")
os.environ.setdefault("PASTOR_CONTACT", "test@example.com")
os.environ.setdefault("ENVIRONMENT", "test")


def load_query_service():
    sys.path.insert(0, str(QUERY_SRC))
    spec = importlib.util.spec_from_file_location(
        "pulpit_query_service_eval",
        QUERY_SRC / "query_service.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_index(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        return payload.get("sermons", [])
    if isinstance(payload, list):
        return payload
    raise ValueError("Index must be a transcripts/index.json payload or a sermon list.")


def load_golden(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("Golden file must contain a JSON list.")
    return payload


def sample_analysis(sample):
    subqueries = sample.get("subqueries") or [sample["question"]]
    return {
        "type": sample.get("type", "detailed"),
        "subqueries": subqueries,
        "scripture_refs": sample.get("scripture_refs", []),
        "language": sample.get("language", "mixed"),
    }


def result_text(result):
    chunks = " ".join(
        chunk.get("text", "")
        for chunk in result.get("matched_chunks", [])
    )
    return " ".join([
        result.get("sermon_id", ""),
        result.get("title", ""),
        result.get("date", ""),
        " ".join(result.get("topics", [])),
        " ".join(result.get("key_themes", [])),
        " ".join(result.get("scripture_references", [])),
        result.get("description", ""),
        chunks,
    ]).lower()


def score_sample(sample, results):
    expected_ids = set(sample.get("expected_sermon_ids") or [])
    expected_terms = [str(term).lower() for term in sample.get("expected_terms") or []]
    result_ids = [result.get("sermon_id", "") for result in results]

    first_rank = None
    if expected_ids:
        for index, sermon_id in enumerate(result_ids, 1):
            if sermon_id in expected_ids:
                first_rank = index
                break

    term_hit = False
    if expected_terms:
        combined = "\n".join(result_text(result) for result in results)
        term_hit = any(term in combined for term in expected_terms)

    if expected_ids:
        passed = first_rank is not None
    elif expected_terms:
        passed = term_hit
    else:
        passed = None

    reciprocal_rank = 1 / first_rank if first_rank else 0.0
    return {
        "passed": passed,
        "has_expected_ids": bool(expected_ids),
        "first_rank": first_rank,
        "reciprocal_rank": reciprocal_rank,
        "term_hit": term_hit,
        "result_ids": result_ids,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", required=True, help="Path to exported transcripts/index.json")
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN), help="Path to golden query JSON")
    parser.add_argument("--use-bedrock", action="store_true", help="Enable Bedrock embeddings during retrieval")
    args = parser.parse_args()

    query_service = load_query_service()
    index = load_index(args.index)
    golden = load_golden(args.golden)

    query_service.get_sermon_index = lambda: index
    query_service.get_retrieval_config = lambda: query_service.DEFAULT_RETRIEVAL_CONFIG
    query_service.rerank_evidence_chunks = lambda _q, _a, hits: hits

    if not args.use_bedrock:
        query_service.embed_text = lambda _text: None

    evaluated = []
    for sample in golden:
        question = sample["question"]
        results = query_service.find_relevant_sermons(
            question,
            query_service.DEFAULT_RETRIEVAL_CONFIG,
            sample_analysis(sample),
        )
        scored = score_sample(sample, results)
        evaluated.append(scored)

        status = "PASS" if scored["passed"] else "FAIL" if scored["passed"] is False else "SKIP"
        top = ", ".join(scored["result_ids"][:5]) or "no results"
        rank = scored["first_rank"] if scored["first_rank"] else "-"
        print(f"{status} {sample.get('id', question)} | rank={rank} | top={top}")

    scored_items = [item for item in evaluated if item["passed"] is not None]
    expected_id_items = [item for item in evaluated if item["has_expected_ids"]]
    pass_count = sum(1 for item in scored_items if item["passed"])
    recall_at_5 = pass_count / len(scored_items) if scored_items else 0.0
    mrr = (
        sum(item["reciprocal_rank"] for item in expected_id_items) / len(expected_id_items)
        if expected_id_items else 0.0
    )

    print("")
    print(f"Queries scored: {len(scored_items)}")
    print(f"Recall proxy @5: {recall_at_5:.3f}")
    print(f"MRR over ID-scored queries: {mrr:.3f}")

    if any(item["passed"] is False for item in scored_items):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
