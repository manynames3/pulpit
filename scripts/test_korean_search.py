#!/usr/bin/env python3
"""Regression tests for Korean lexical matching and bilingual query expansion."""

import importlib.util
import os
import sys
from collections import Counter
from datetime import datetime, timezone
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


def test_packaged_synonyms_loaded():
    assert query_service.PACKAGED_SYNONYM_CONFIG.get("version") == "v1"
    assert "돈" in query_service.STATIC_QUERY_VARIANTS["money"]
    assert query_service.ENGLISH_LEXICAL_ALIASES["failures"] == "fail"


def test_bm25_chunk_scoring_prefers_specific_metadata_match():
    index = [
        {
            "sermon_id": "specific",
            "title": "구름기둥 설교",
            "date": "2026-01-01",
            "topics": ["구름기둥"],
            "key_themes": [],
            "scripture_references": ["출애굽기"],
            "description": "",
            "chunks": [
                {
                    "chunk_id": "specific:1",
                    "text": "하나님께서 구름기둥으로 인도하셨습니다.",
                    "metadata_terms": ["구름기둥", "출애굽기"],
                    "korean_tokens": ["구름기둥"],
                }
            ],
        },
        {
            "sermon_id": "generic",
            "title": "광야 설교",
            "date": "2026-01-02",
            "topics": [],
            "key_themes": [],
            "scripture_references": [],
            "description": "",
            "chunks": [
                {
                    "chunk_id": "generic:1",
                    "text": "광야에서 하나님을 바라봅니다.",
                    "metadata_terms": [],
                }
            ],
        },
    ]
    terms = query_service.collect_search_terms(["cloud column", "구름기둥"])
    stats = query_service.build_bm25_stats(index, terms)
    specific = query_service.chunk_lexical_match_score(index[0], terms, index[0]["chunks"][0], stats)
    generic = query_service.chunk_lexical_match_score(index[1], terms, index[1]["chunks"][0], stats)

    assert specific > generic
    assert specific > 0


def test_source_snippets_are_bounded():
    sermon = {
        "matched_chunks": [
            {
                "text": " ".join(["snippet"] * 100),
                "score": 1.23456,
            }
        ]
    }

    snippets = query_service.source_snippets(sermon)
    assert len(snippets) == 1
    assert len(snippets[0]["text"]) <= query_service.SOURCE_SNIPPET_CHAR_LIMIT
    assert snippets[0]["score"] == 1.2346


def test_answer_cache_key_tracks_index_marker():
    first = query_service.question_hash("money", "en", query_service.DEFAULT_RETRIEVAL_CONFIG, "index-a")
    second = query_service.question_hash("money", "en", query_service.DEFAULT_RETRIEVAL_CONFIG, "index-b")
    assert first != second


def test_index_marker_change_clears_warm_index_cache():
    original_s3 = query_service.s3
    original_marker = query_service._index_marker
    original_marker_loaded_at = query_service._index_marker_loaded_at
    original_index = query_service._sermon_index
    original_index_loaded_at = query_service._index_loaded_at
    original_generated_at = query_service._index_generated_at

    class FakeS3:
        def __init__(self):
            self.etag = "etag-a"

        def head_object(self, Bucket, Key):
            return {
                "ETag": self.etag,
                "LastModified": datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
                "ContentLength": 123,
            }

    fake_s3 = FakeS3()

    try:
        query_service.s3 = fake_s3
        query_service._index_marker = ""
        query_service._index_marker_loaded_at = None
        query_service._sermon_index = [{"sermon_id": "old"}]
        query_service._index_loaded_at = datetime.now(timezone.utc)
        query_service._index_generated_at = "old"

        first_marker = query_service.get_index_cache_marker()
        assert first_marker
        assert query_service._sermon_index == [{"sermon_id": "old"}]

        fake_s3.etag = "etag-b"
        query_service._index_marker_loaded_at = None
        second_marker = query_service.get_index_cache_marker()

        assert second_marker != first_marker
        assert query_service._sermon_index is None
        assert query_service._index_loaded_at is None
        assert query_service._index_generated_at == ""
    finally:
        query_service.s3 = original_s3
        query_service._index_marker = original_marker
        query_service._index_marker_loaded_at = original_marker_loaded_at
        query_service._sermon_index = original_index
        query_service._index_loaded_at = original_index_loaded_at
        query_service._index_generated_at = original_generated_at


def test_archive_stats_keeps_lessons_distinct_from_topics():
    stats = query_service.build_archive_stats([
        {
            "sermon_id": "first",
            "date": "2026-01-01",
            "duration_seconds": 1800,
            "topics": ["Grace"],
            "key_themes": [],
        },
        {
            "sermon_id": "second",
            "date": "2026-01-02",
            "duration_seconds": 1200,
            "topics": ["Grace"],
            "key_themes": [],
        },
    ])

    assert stats["top_topics"][0]["label"] == "Grace"
    assert stats["top_topics"][0]["count"] == 2
    assert stats["top_lessons"] == []


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
    test_packaged_synonyms_loaded()
    test_bm25_chunk_scoring_prefers_specific_metadata_match()
    test_source_snippets_are_bounded()
    test_answer_cache_key_tracks_index_marker()
    test_index_marker_change_clears_warm_index_cache()
    test_archive_stats_keeps_lessons_distinct_from_topics()
    test_retrieve_union_diversifies_broad_keyword_results()
    print("Korean search tests passed")
