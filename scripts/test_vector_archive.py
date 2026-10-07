#!/usr/bin/env python3
"""Offline regression checks for snapshot consistency and bounded retrieval."""

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambda/query"))
from archive_store import ArchiveStore, ArchiveUnavailable, MANIFEST_KEY, publish_snapshot
import test_korean_search as legacy_tests
service = legacy_tests.query_service


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.reads = []

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        self.reads.append(Key)
        return {"Body": io.BytesIO(self.objects[Key])}


class FakeVectors:
    def __init__(self):
        self.items = {}
        self.incomplete = False
        self.filters = []

    def get_index(self, **kwargs):
        return {"index": {"dimension": 256, "distanceMetric": "cosine"}}

    def put_vectors(self, vectors, **kwargs):
        self.items.update({v["key"]: v for v in vectors})

    def get_vectors(self, keys, **kwargs):
        return {"vectors": [] if self.incomplete else [self.items[k] for k in keys]}

    def query_vectors(self, filter, **kwargs):
        self.filters.append(filter)
        return {"vectors": [{**v, "distance": 0.1} for v in self.items.values()
                            if v["metadata"]["snapshot"] == filter["snapshot"]]}


def fixture():
    return {"generated_at": "2026-10-07", "sermons": [
        {"sermon_id": "ark", "title": "Noah", "date": "2026-01-01", "chunks": [
            {"chunk_id": "ark:0", "chunk_index": 0, "text": "노아의 방주를 지으라. 고고학자들이 발굴한 유적. failures",
             "embedding": [0.1] * 256},
            {"chunk_id": "ark:1", "chunk_index": 1, "text": "하나님의 인도하심", "embedding": [0.2] * 256}]},
        {"sermon_id": "money", "title": "돈", "date": "2026-02-01", "chunks": [
            {"chunk_id": "money:0", "chunk_index": 0, "text": "돈을 사랑하지 말라", "embedding": [0.3] * 256}]}]}


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.s3, self.vectors = FakeS3(), FakeVectors()
        self.aliases = service.ENGLISH_LEXICAL_ALIASES
        self.manifest = publish_snapshot(self.s3, self.vectors, "bucket", fixture(), "index", self.aliases)
        self.store = ArchiveStore(self.s3, self.vectors, "bucket")

    def test_catalog_never_reads_vectors_or_legacy_index(self):
        manifest = self.store.load_manifest()
        self.assertEqual(len(manifest["sermons"]), 2)
        self.assertNotIn("chunks", manifest["sermons"][0])
        self.assertNotIn("embedding", manifest["sermons"][0])
        self.assertEqual(self.s3.reads, [MANIFEST_KEY])

    def test_korean_morphology_and_english_forms(self):
        for query, expected in [("방주", "ark"), ("고고학", "ark"), ("돈", "money"), ("fail", "ark")]:
            hits = self.store.lexical_candidates(self.manifest, [query], self.aliases, service.term_count)
            self.assertTrue(hits, query)
            self.assertEqual(hits[0][1], expected)
        self.assertTrue(all("/lexical/" in k for k in self.s3.reads))

    def test_failed_import_preserves_active_snapshot(self):
        before = self.s3.objects[MANIFEST_KEY]
        changed = fixture()
        changed["generated_at"] = "next"
        self.vectors.incomplete = True
        with self.assertRaises(ArchiveUnavailable):
            publish_snapshot(self.s3, self.vectors, "bucket", changed, "index", self.aliases)
        self.assertEqual(self.s3.objects[MANIFEST_KEY], before)

    def test_snapshot_filter_excludes_old_chunks(self):
        changed = fixture()
        changed["sermons"] = changed["sermons"][:1]
        manifest = publish_snapshot(self.s3, self.vectors, "bucket", changed, "index", self.aliases)
        hits = self.store.semantic_candidates(manifest, [0.1] * 256)
        self.assertEqual({h[1] for h in hits}, {"ark"})
        self.assertEqual(self.vectors.filters[-1], {"snapshot": manifest["snapshot"]})

    def test_runtime_union_hides_sermons_and_keeps_neighbors(self):
        with patch.object(service, "archive_store", self.store), patch.object(service, "_archive_manifest", self.manifest), \
             patch.object(service, "embed_text", return_value=[0.1] * 256), \
             patch.object(self.store, "semantic_candidates", return_value=[]), \
             patch.object(service, "rerank_evidence_chunks", side_effect=lambda q, a, hits: hits):
            index = [s for s in self.manifest["sermons"] if s["sermon_id"] == "ark"]
            result = service.find_relevant_sermons_from_vectors(index, "고고학", {}, {})
        self.assertEqual([s["sermon_id"] for s in result], ["ark"])
        self.assertTrue(any(c["neighbor"] for c in result[0]["matched_chunks"]))
        self.assertTrue(all("transcripts/index.json" != k for k in self.s3.reads))

    def test_invalid_embeddings_do_not_publish(self):
        before = self.s3.objects[MANIFEST_KEY]
        payload = fixture()
        payload["sermons"][0]["chunks"][0]["embedding"] = [float("nan")] * 256
        with self.assertRaises(ValueError):
            publish_snapshot(self.s3, self.vectors, "bucket", payload, "index", self.aliases)
        self.assertEqual(self.s3.objects[MANIFEST_KEY], before)

    def test_answer_cache_scores_round_trip_through_dynamodb(self):
        table = Mock()
        result = {"answer": "Answer", "sources": [{"snippets": [{"score": 0.65}]}]}
        with patch.object(service.dynamodb, "Table", return_value=table):
            service.cache_answer("question", result, index_marker="snapshot")
            item = table.put_item.call_args.kwargs["Item"]
            TypeSerializer().serialize(item)
            table.get_item.return_value = {"Item": item}
            cached = service.check_cache("question", index_marker="snapshot")
        self.assertEqual(cached["sources"][0]["snippets"][0]["score"], 0.65)
        self.assertEqual(json.loads(service.response(200, cached)["body"])["answer"], "Answer")

    def test_numeric_rerank_ids_are_validated_and_deduplicated(self):
        entry = fixture()["sermons"][0]
        hits = [service.build_chunk_hit(entry, chunk, 0.5, 1, 0.8) for chunk in entry["chunks"]]
        reply = {"output": {"message": {"content": [{"text": '{"candidate_ids":[2,2,0,99,true]}' }]}}}
        with patch.object(service, "get_intermediate_cache", return_value=None), \
             patch.object(service, "put_intermediate_cache"), \
             patch.object(service.bedrock, "converse", return_value=reply):
            result = service.rerank_evidence_chunks("question", {}, hits)
        self.assertEqual([hit["chunk_id"] for hit in result], ["ark:1", "ark:0"])

    def test_cache_poll_never_starts_model_work(self):
        with patch.object(service, "get_retrieval_config", return_value={}), \
             patch.object(service, "get_index_cache_marker", return_value="snapshot"), \
             patch.object(service, "check_cache", return_value=None), \
             patch.object(service, "analyze_question") as planner:
            result = service.answer_question("question", cache_only=True)
        self.assertEqual(result, {"processing": True})
        planner.assert_not_called()

    def test_bedrock_outage_keeps_sources_and_uses_short_cache(self):
        error = ClientError({"Error": {"Code": "ServiceUnavailableException"}}, "Converse")
        with patch.multiple(service,
            get_retrieval_config=Mock(return_value={}), get_index_cache_marker=Mock(return_value="snapshot"),
            check_cache=Mock(return_value=None), analyze_question=Mock(return_value={}),
            find_relevant_sermons=Mock(return_value=fixture()["sermons"]),
            invoke_bedrock=Mock(side_effect=error), cache_answer=Mock(), log_query=Mock(), log_retrieval_eval=Mock()):
            result = service.answer_question("cloud column")
        self.assertTrue(result["answer_generation_unavailable"])
        self.assertEqual(len(result["sources"]), 2)
        table = Mock()
        with patch.object(service.dynamodb, "Table", return_value=table):
            service.cache_answer("cloud column", result, index_marker="snapshot")
            item = table.put_item.call_args.kwargs["Item"]
            cached_at = service.datetime.fromisoformat(item["cachedAt"]).timestamp()
            self.assertLessEqual(item["expiresAt"] - cached_at, 60)
            item["expiresAt"] = 1
            table.get_item.return_value = {"Item": item}
            self.assertIsNone(service.check_cache("cloud column", index_marker="snapshot"))


if __name__ == "__main__":
    unittest.main()
