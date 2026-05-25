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
import re
from collections import Counter
from datetime import datetime, timezone

import boto3


EMBED_MODEL_ID = os.environ.get("PULPIT_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")
CHUNK_WORDS = int(os.environ.get("PULPIT_CHUNK_WORDS", "180"))
CHUNK_OVERLAP_WORDS = int(os.environ.get("PULPIT_CHUNK_OVERLAP_WORDS", "40"))
MAX_EMBED_CHARS = int(os.environ.get("PULPIT_MAX_EMBED_CHARS", "8000"))
DEFAULT_INDEX_KEY = os.environ.get("PULPIT_INDEX_KEY", "transcripts/index.json")
DEFAULT_PREFIX = os.environ.get("PULPIT_TRANSCRIPT_PREFIX", "transcripts/")
TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
ASCII_TERM_RE = re.compile(r"^[a-z0-9]+$")
HANGUL_RE = re.compile(r"[가-힣]")
KO_TOKEN_RE = re.compile(r"[0-9A-Za-z\uAC00-\uD7A3\u1100-\u11FF\u3130-\u318F]+")
KO_TOKEN_TRIM = ".,!?;:'\"()[]{}「」『』【】—–…·"
ENGLISH_STOP_TERMS = {
    "about", "after", "again", "all", "and", "are", "because", "been",
    "but", "can", "did", "does", "for", "from", "had", "has", "have",
    "his", "into", "its", "may", "not", "our", "sermon", "sermons",
    "she", "that", "the", "their", "there", "this", "through", "was",
    "were", "what", "when", "where", "which", "with", "you", "your",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def sha256_text(text):
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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


def normalize_english_token(token):
    token = (token or "").lower().strip()
    if not ASCII_TERM_RE.fullmatch(token):
        return token
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 5 and token.endswith("ing"):
        base = token[:-3]
        if len(base) >= 3 and base[-1] == base[-2]:
            base = base[:-1]
        return base
    if len(token) > 4 and token.endswith("ed"):
        base = token[:-2]
        if len(base) >= 3 and base[-1] == base[-2]:
            base = base[:-1]
        return base
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def english_tokens(text, limit=40):
    counts = Counter()
    for token in TOKEN_RE.findall(text or ""):
        normalized = normalize_english_token(token.lower())
        if not ASCII_TERM_RE.fullmatch(normalized):
            continue
        if len(normalized) < 3 or normalized in ENGLISH_STOP_TERMS:
            continue
        counts[normalized] += 1
    return [term for term, _ in counts.most_common(limit)]


def korean_tokens(text, limit=60):
    counts = Counter()
    for raw in KO_TOKEN_RE.findall(text or ""):
        token = raw.strip(KO_TOKEN_TRIM).lower()
        if not token or not HANGUL_RE.search(token) or len(token) < 2:
            continue
        counts[token] += 1
    return [term for term, _ in counts.most_common(limit)]


def normalized_metadata_terms(sermon):
    values = []
    for field in ("topics", "key_themes", "scripture_references"):
        value = sermon.get(field, [])
        if isinstance(value, str):
            value = re.split(r"[,;\n]+", value)
        if isinstance(value, list):
            values.extend(value)

    terms = []
    seen = set()
    for value in values:
        term = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n.,!?;:'\"()[]{}")
        key = term.lower()
        if not term or key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return terms


def chunk_search_text(sermon, chunk_text):
    return "\n".join([
        sermon.get("title", ""),
        " ".join(sermon.get("topics", [])),
        " ".join(sermon.get("key_themes", [])),
        " ".join(sermon.get("scripture_references", [])),
        sermon.get("description", ""),
        chunk_text or "",
    ]).strip()


def enrich_chunk_metadata(sermon, chunk_text):
    search_text = chunk_search_text(sermon, chunk_text)
    metadata_terms = normalized_metadata_terms(sermon)
    return {
        "search_text": search_text,
        "metadata_terms": metadata_terms,
        "english_tokens": english_tokens(search_text),
        "korean_tokens": korean_tokens(search_text),
    }


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
    topics = sermon.get("topics", [])
    key_themes = sermon.get("key_themes", [])

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
        chunk_metadata = enrich_chunk_metadata(sermon, chunk["text"])

        chunks.append({
            "chunk_id": f"{sermon['sermon_id']}:{chunk['chunk_index']}",
            "chunk_index": chunk["chunk_index"],
            "chunk_hash": chunk_hash,
            "word_start": chunk["word_start"],
            "word_end": chunk["word_end"],
            "duration": sermon.get("duration", ""),
            "duration_seconds": safe_int(sermon.get("duration_seconds")),
            "text": chunk["text"],
            **chunk_metadata,
            "embedding": chunk_embedding,
        })

    return {
        "sermon_id": sermon.get("sermon_id", ""),
        "title": sermon.get("title", ""),
        "date": sermon.get("date", ""),
        "youtube_url": sermon.get("youtube_url", ""),
        "duration": sermon.get("duration", ""),
        "duration_seconds": safe_int(sermon.get("duration_seconds")),
        "description": sermon.get("description", ""),
        "pastor_name": sermon.get("pastor_name", ""),
        "scripture_references": sermon.get("scripture_references", []),
        "topics": topics,
        "key_themes": key_themes,
        "embedding": sermon_embedding,
        "transcript_hash": transcript_hash,
        "transcript_word_count": len(transcript.split()),
        "chunks": chunks,
    }


def validate_index_embeddings(index_payload):
    sermons = index_payload.get("sermons", [])
    missing_sermon_embeddings = [
        entry.get("sermon_id") or entry.get("title", "")
        for entry in sermons
        if not entry.get("embedding")
    ]
    missing_chunk_embeddings = []

    for entry in sermons:
        for chunk in entry.get("chunks", []):
            if not chunk.get("embedding"):
                missing_chunk_embeddings.append(chunk.get("chunk_id", "unknown"))

    if missing_sermon_embeddings or missing_chunk_embeddings:
        message = (
            f"Index validation failed: {len(missing_sermon_embeddings)} sermon embeddings missing, "
            f"{len(missing_chunk_embeddings)} chunk embeddings missing"
        )
        if os.environ.get("PULPIT_ALLOW_INCOMPLETE_INDEX") == "1":
            print(f"WARNING: {message}")
            return
        raise RuntimeError(message)


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
        "chunk_embedding_count": sum(
            1
            for entry in sermons
            for chunk in entry.get("chunks", [])
            if chunk.get("embedding")
        ),
        "chunk_embedding_complete": all(
            chunk.get("embedding")
            for entry in sermons
            for chunk in entry.get("chunks", [])
        ),
        "sermons": sermons,
    }
    validate_index_embeddings(index_payload)

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
