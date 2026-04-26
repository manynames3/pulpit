#!/usr/bin/env python3
"""
Pulpit — Rebuild Search Index

Builds a chunked hybrid-search index in S3 from the raw sermon JSON files.
This keeps the architecture on the cheap path:
  - S3 for storage
  - Bedrock Titan for embeddings
  - Lambda for retrieval/ranking

No OpenSearch cluster, no vector database, no idle search bill.

The script reuses existing embeddings when transcript/chunk hashes have not changed,
so reruns are inexpensive after the first chunked build.
"""

import hashlib
import json
import os
from datetime import datetime, timezone

import boto3


EMBED_MODEL_ID = os.environ.get("PULPIT_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")
CHUNK_WORDS = int(os.environ.get("PULPIT_CHUNK_WORDS", "180"))
CHUNK_OVERLAP_WORDS = int(os.environ.get("PULPIT_CHUNK_OVERLAP_WORDS", "40"))
MAX_EMBED_CHARS = int(os.environ.get("PULPIT_MAX_EMBED_CHARS", "8000"))
DEFAULT_INDEX_KEY = os.environ.get("PULPIT_INDEX_KEY", "transcripts/index.json")
DEFAULT_PREFIX = os.environ.get("PULPIT_TRANSCRIPT_PREFIX", "transcripts/")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def sha256_text(text):
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def embed_text(bedrock, text):
    payload = {
        "inputText": text[:MAX_EMBED_CHARS],
        "dimensions": 256,
        "normalize": True,
    }
    resp = bedrock.invoke_model(modelId=EMBED_MODEL_ID, body=json.dumps(payload))
    return json.loads(resp["body"].read())["embedding"]


def list_sermon_keys(s3, bucket, prefix):
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item["Key"]
            if not key.endswith(".json"):
                continue
            if key.endswith("/index.json"):
                continue
            if "/skips/" in key:
                continue
            keys.append(key)
    return sorted(keys)


def load_json_s3(s3, bucket, key):
    raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(raw)


def load_existing_index(s3, bucket, index_key):
    try:
        return load_json_s3(s3, bucket, index_key)
    except Exception:
        return {"sermons": []}


def build_existing_maps(existing_index):
    sermons_by_id = {}
    chunks_by_hash = {}

    for entry in existing_index.get("sermons", []):
        sermon_id = entry.get("sermon_id")
        if sermon_id:
            sermons_by_id[sermon_id] = entry

        for chunk in entry.get("chunks", []):
            chunk_hash = chunk.get("chunk_hash")
            embedding = chunk.get("embedding")
            if chunk_hash and embedding:
                chunks_by_hash[chunk_hash] = embedding

    return sermons_by_id, chunks_by_hash


def chunk_transcript(transcript):
    words = (transcript or "").split()
    if not words:
        return []

    step = max(CHUNK_WORDS - CHUNK_OVERLAP_WORDS, 1)
    chunks = []
    start = 0
    chunk_index = 1

    while start < len(words):
        window = words[start:start + CHUNK_WORDS]
        text = " ".join(window).strip()
        if text:
            chunks.append({
                "chunk_index": chunk_index,
                "word_start": start,
                "word_end": start + len(window),
                "text": text,
            })
            chunk_index += 1

        if start + CHUNK_WORDS >= len(words):
            break
        start += step

    return chunks


def sermon_embed_input(sermon):
    fields = [
        sermon.get("title", ""),
        " ".join(sermon.get("topics", [])),
        " ".join(sermon.get("key_themes", [])),
        " ".join(sermon.get("scripture_references", [])),
        sermon.get("description", ""),
        sermon.get("transcript", ""),
    ]
    return "\n".join(part.strip() for part in fields if part).strip()


def build_sermon_entry(sermon, existing_entry, chunk_embedding_cache, bedrock):
    transcript = sermon.get("transcript", "") or ""
    transcript_hash = sha256_text(transcript)

    existing_hash = (existing_entry or {}).get("transcript_hash")
    if existing_hash == transcript_hash and existing_entry and existing_entry.get("embedding"):
        sermon_embedding = existing_entry["embedding"]
    else:
        sermon_embedding = sermon.get("embedding") or embed_text(bedrock, sermon_embed_input(sermon))

    chunks = []
    for chunk in chunk_transcript(transcript):
        chunk_hash = sha256_text(chunk["text"])
        chunk_embedding = chunk_embedding_cache.get(chunk_hash)
        if not chunk_embedding:
            chunk_embedding = embed_text(bedrock, chunk["text"])
            chunk_embedding_cache[chunk_hash] = chunk_embedding

        chunks.append({
            "chunk_id": f"{sermon['sermon_id']}:{chunk['chunk_index']}",
            "chunk_index": chunk["chunk_index"],
            "chunk_hash": chunk_hash,
            "word_start": chunk["word_start"],
            "word_end": chunk["word_end"],
            "text": chunk["text"],
            "embedding": chunk_embedding,
        })

    return {
        "sermon_id": sermon.get("sermon_id", ""),
        "title": sermon.get("title", ""),
        "date": sermon.get("date", ""),
        "youtube_url": sermon.get("youtube_url", ""),
        "description": sermon.get("description", ""),
        "pastor_name": sermon.get("pastor_name", ""),
        "scripture_references": sermon.get("scripture_references", []),
        "topics": sermon.get("topics", []),
        "key_themes": sermon.get("key_themes", []),
        "embedding": sermon_embedding,
        "transcript_hash": transcript_hash,
        "transcript_word_count": len(transcript.split()),
        "chunks": chunks,
    }


def rebuild_index(bucket, region="us-east-1", prefix=DEFAULT_PREFIX, index_key=DEFAULT_INDEX_KEY):
    s3 = boto3.client("s3", region_name=region)
    bedrock = boto3.client("bedrock-runtime", region_name=region)

    keys = list_sermon_keys(s3, bucket, prefix)
    existing_index = load_existing_index(s3, bucket, index_key)
    existing_sermons, chunk_embedding_cache = build_existing_maps(existing_index)

    print(f"Rebuilding search index from s3://{bucket}/{prefix}")
    print(f"Transcript files found: {len(keys)}")
    print(f"Reusable chunk embeddings: {len(chunk_embedding_cache)}")

    sermons = []
    embedded_sermons = 0
    embedded_chunks = 0

    for idx, key in enumerate(keys, 1):
        sermon = load_json_s3(s3, bucket, key)
        sermon_id = sermon.get("sermon_id")
        existing_entry = existing_sermons.get(sermon_id)
        before_cache_size = len(chunk_embedding_cache)

        entry = build_sermon_entry(sermon, existing_entry, chunk_embedding_cache, bedrock)
        if not existing_entry or existing_entry.get("transcript_hash") != entry.get("transcript_hash"):
            embedded_sermons += int(bool(entry.get("embedding")))
        embedded_chunks += max(len(chunk_embedding_cache) - before_cache_size, 0)
        sermons.append(entry)

        print(f"[{idx}/{len(keys)}] {entry['title']}  | chunks={len(entry['chunks'])}")

    sermons.sort(key=lambda item: (item.get("date", ""), item.get("title", "")), reverse=True)
    chunk_count = sum(len(entry.get("chunks", [])) for entry in sermons)

    index_payload = {
        "generated_at": now_iso(),
        "sermon_count": len(sermons),
        "embedding_count": sum(1 for entry in sermons if entry.get("embedding")),
        "chunk_count": chunk_count,
        "chunk_embedding_count": sum(len(entry.get("chunks", [])) for entry in sermons),
        "sermons": sermons,
    }

    s3.put_object(
        Bucket=bucket,
        Key=index_key,
        Body=json.dumps(index_payload, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )

    print(f"\nUploaded index to s3://{bucket}/{index_key}")
    print(f"Sermons indexed: {len(sermons)}")
    print(f"Chunks indexed:  {chunk_count}")
    print(f"New sermon embeddings this run: {embedded_sermons}")
    print(f"New chunk embeddings this run:  {embedded_chunks}")


if __name__ == "__main__":
    bucket = os.environ["PULPIT_TRANSCRIPT_BUCKET"]
    region = os.environ.get("AWS_REGION", "us-east-1")
    rebuild_index(bucket=bucket, region=region)
