#!/usr/bin/env python3
"""Regression tests for Korean lexical matching and bilingual query expansion."""

import importlib.util
import os
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUERY_SRC = ROOT / "lambda" / "query"
sys.path.insert(0, str(QUERY_SRC))

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

spec = importlib.util.spec_from_file_location(
    "pulpit_query_service",
    QUERY_SRC / "query_service.py",
)
query_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(query_service)


def test_korean_lexical_score():
    assert query_service.korean_lexical_score("고고학", "고고학자들이 발굴한 유적") > 0
    assert query_service.korean_lexical_score("고고학적", "고고학 연구에 따르면") > 0
    assert query_service.korean_lexical_score("과학", "과학자가 설명하기를") > 0
    assert query_service.korean_lexical_score("성도", "성도들이 모여서") > 0

    assert query_service.korean_lexical_score("의", "은혜의 하나님") == 0.0
    assert query_service.korean_lexical_score("가", "믿음으로 가는 길") == 0.0


def test_expand_query():
    assert "노아" in query_service.expand_query("noah")
    assert "노아의 방주" in query_service.expand_query("noah's ark")
    assert "방주" in query_service.expand_query("noah's ark")
    assert "noah" in query_service.expand_query("노아")
    assert "은혜" in query_service.expand_query("grace")
    assert "grace" in query_service.expand_query("은혜")
    assert "돈" in query_service.expand_query("money")
    assert "money" in query_service.expand_query("돈")
    assert "구름기둥" in query_service.expand_query("cloud column")
    assert "cloud column" in query_service.expand_query("구름기둥")


def test_retrieve_union_diversifies_broad_keyword_results():
    original_embed_text = query_service.embed_text
    original_build_query_bundle = query_service.build_query_bundle

    try:
        query_service.embed_text = lambda _text: None
        query_service.build_query_bundle = (
            lambda _index, question, include_llm_expansion=False: [question]
        )

        index = []
        for sermon_number in range(8):
            sermon_id = f"sermon-{sermon_number}"
            index.append({
                "sermon_id": sermon_id,
                "title": f"광야 sermon {sermon_number}",
                "date": f"2026-01-{sermon_number + 1:02d}",
                "pastor_name": "이혜진 목사",
                "topics": [],
                "scripture_references": [],
                "description": "",
                "chunks": [
                    {
                        "chunk_id": f"{sermon_id}:{chunk_number}",
                        "sermon_id": sermon_id,
                        "chunk_index": chunk_number,
                        "text": "광야 훈련과 순종에 대한 설교 내용입니다.",
                    }
                    for chunk_number in range(5)
                ],
            })

        hits = query_service.retrieve_union(["광야"], index, top_n=10)
        sermon_counts = Counter(hit["sermon_id"] for hit in hits)

        assert len(hits) == 10
        assert len(sermon_counts) >= 4
        assert max(sermon_counts.values()) <= query_service.CHUNK_DIVERSITY_PER_SERMON
    finally:
        query_service.embed_text = original_embed_text
        query_service.build_query_bundle = original_build_query_bundle


if __name__ == "__main__":
    test_korean_lexical_score()
    test_expand_query()
    test_retrieve_union_diversifies_broad_keyword_results()
    print("Korean search tests passed")
