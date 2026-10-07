"""Versioned S3 Vectors snapshots and sharded lexical retrieval, without model calls."""

import gzip
import hashlib
import json
import math
import re
import threading
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor

TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
HANGUL_RE = re.compile(r"[가-힣]")
SINGLE_TERMS = set("돈죄시창출민신왕마막눅요행롬계")
FIELD_WEIGHTS = {
    "title": 4.0, "topics": 3.4, "key_themes": 3.0,
    "scripture_references": 3.2, "description": 1.6,
    "metadata_terms": 2.0, "text": 1.0,
}
MANIFEST_KEY = "indexes/retrieval/manifest.json"
SCHEMA_VERSION = 1
MAX_CANDIDATES = 200


class ArchiveUnavailable(RuntimeError):
    pass


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def english_token(token, aliases):
    if token in aliases:
        return aliases[token]
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 5 and token.endswith("ing"):
        base = token[:-3]
        return base[:-1] if len(base) >= 3 and base[-1] == base[-2] else base
    if len(token) > 4 and token.endswith("ed"):
        base = token[:-2]
        return base[:-1] if len(base) >= 3 and base[-1] == base[-2] else base
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def tokens(text, aliases):
    return [english_token(t.lower(), aliases) if not HANGUL_RE.search(t) else t.lower()
            for t in TOKEN_RE.findall(text or "") if len(t) >= 2 or t in SINGLE_TERMS]


def term_groups(token):
    # Korean substring groups locate candidates; the runtime morphology matcher
    # verifies actual tokens before scoring. They are not substring search results.
    if HANGUL_RE.search(token):
        return {"ko:" + token[i:i + 2] for i in range(len(token) - 1)} | {
            "ko:" + char for char in token if char in SINGLE_TERMS}
    return {"en:" + token}


def shard_for(group):
    return hashlib.sha256(group.encode()).hexdigest()[:2]


def build_snapshot(payload, index_arn, aliases):
    sermons = payload.get("sermons")
    if not isinstance(sermons, list) or not sermons:
        raise ValueError("Refusing to publish an empty or invalid archive")
    snapshot = hashlib.sha256(json_bytes([SCHEMA_VERSION, payload, aliases])).hexdigest()[:24]
    prefix = f"indexes/retrieval/snapshots/{snapshot}"
    catalog, files, vectors, documents = [], {}, [], []
    seen = set()
    for sermon in sermons:
        sid = sermon.get("sermon_id", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", sid) or not sermon.get("chunks"):
            raise ValueError(f"Invalid sermon or missing chunks: {sid}")
        if sid in seen:
            raise ValueError(f"Duplicate sermon: {sid}")
        seen.add(sid)
        metadata = {k: v for k, v in sermon.items() if k not in {
            "embedding", "chunks", "transcript", "search_text"}}
        catalog.append(metadata)
        chunks = []
        for chunk in sermon["chunks"]:
            cid = chunk.get("chunk_id")
            embedding = chunk.get("embedding")
            if not cid or not embedding or len(embedding) != 256 or not all(
                isinstance(x, (int, float)) and math.isfinite(x) for x in embedding
            ) or not any(embedding):
                raise ValueError(f"Invalid 256-dimension embedding: {cid}")
            vector_key = f"{snapshot}:{cid}"
            if vector_key in seen:
                raise ValueError(f"Duplicate chunk: {cid}")
            seen.add(vector_key)
            vectors.append({"key": vector_key, "data": {"float32": embedding},
                            "metadata": {"snapshot": snapshot, "sermon_id": sid, "chunk_id": cid}})
            clean = {k: v for k, v in chunk.items() if k not in {
                "embedding", "search_text", "english_tokens", "korean_tokens"}}
            chunks.append(clean)
            counts = Counter()
            for field, weight in FIELD_WEIGHTS.items():
                value = clean.get(field, "") if field in {"text", "metadata_terms"} else metadata.get(field, "")
                if isinstance(value, list):
                    value = " ".join(str(x) for x in value)
                for token, count in Counter(tokens(str(value), aliases)).items():
                    counts[token] += count * weight
            documents.append((cid, sid, counts))
        files[f"{prefix}/sermons/{sid}.json.gz"] = gzip.compress(json_bytes(chunks), mtime=0)
    postings = {}
    total_length = 0
    for cid, sid, counts in documents:
        length = max(sum(counts.values()), 1)
        total_length += length
        for token, frequency in counts.items():
            for group in term_groups(token):
                shard = postings.setdefault(shard_for(group), {})
                shard.setdefault(group, {}).setdefault(token, []).append([cid, sid, frequency, length])
    for shard, contents in postings.items():
        files[f"{prefix}/lexical/{shard}.json.gz"] = gzip.compress(json_bytes(contents), mtime=0)
    manifest = {
        "schema_version": SCHEMA_VERSION, "snapshot": snapshot, "prefix": prefix,
        "generated_at": payload.get("generated_at", ""), "index_arn": index_arn,
        "embedding_model": "amazon.titan-embed-text-v2:0", "dimensions": 256,
        "chunk_count": len(vectors), "avgdl": total_length / len(vectors),
        "lexical_shards": sorted(postings), "sermons": catalog,
    }
    return manifest, files, vectors


def publish_snapshot(s3, vectors_client, bucket, payload, index_arn, aliases, manifest_key=MANIFEST_KEY):
    manifest, files, vectors = build_snapshot(payload, index_arn, aliases)
    index = vectors_client.get_index(indexArn=index_arn)["index"]
    if index["dimension"] != 256 or index["distanceMetric"] != "cosine":
        raise ValueError("Index must use 256 dimensions and cosine distance")
    for offset in range(0, len(vectors), 250):
        vectors_client.put_vectors(indexArn=index_arn, vectors=vectors[offset:offset + 250])
    # Check every uploaded key before publishing the manifest pointer.
    for offset in range(0, len(vectors), 100):
        keys = [v["key"] for v in vectors[offset:offset + 100]]
        found = vectors_client.get_vectors(indexArn=index_arn, keys=keys,
                                          returnMetadata=True, returnData=False)["vectors"]
        if {v["key"] for v in found} != set(keys):
            raise ArchiveUnavailable("Vector import is incomplete; active manifest unchanged")
    probe = vectors_client.query_vectors(indexArn=index_arn, topK=1,
        queryVector=vectors[0]["data"], filter={"snapshot": manifest["snapshot"]}, returnMetadata=True)
    if not probe.get("vectors"):
        raise ArchiveUnavailable("Snapshot is not searchable; active manifest unchanged")
    def upload(item):
        key, body = item
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json", ContentEncoding="gzip")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(upload, files.items()))
    s3.put_object(Bucket=bucket, Key=manifest_key, Body=json_bytes(manifest), ContentType="application/json")
    return manifest


class ArchiveStore:
    def __init__(self, s3, vectors, bucket, manifest_key=MANIFEST_KEY):
        self.s3, self.vectors, self.bucket, self.manifest_key = s3, vectors, bucket, manifest_key
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.cache_lock = threading.Lock()

    def load_manifest(self):
        data = json.loads(self.s3.get_object(Bucket=self.bucket, Key=self.manifest_key)["Body"].read())
        if data.get("schema_version") != SCHEMA_VERSION or data.get("dimensions") != 256:
            raise ArchiveUnavailable("Unsupported archive snapshot")
        return data

    def read_compressed(self, key):
        with self.cache_lock:
            body = self.cache.pop(key, None)
            if body is not None:
                self.cache[key] = body
        if body is None:
            body = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            # Cache compressed bytes, not expanded Python objects; bound warm memory.
            with self.cache_lock:
                while self.cache and self.cache_bytes + len(body) > 8 * 1024 * 1024:
                    _, old = self.cache.popitem(last=False)
                    self.cache_bytes -= len(old)
                if len(body) <= 8 * 1024 * 1024 and key not in self.cache:
                    self.cache[key] = body
                    self.cache_bytes += len(body)
        return json.loads(gzip.decompress(body))

    def lexical_candidates(self, manifest, terms, aliases, matcher, limit=MAX_CANDIDATES):
        scores = Counter()
        for term in dict.fromkeys(terms):
            groups = set().union(*(term_groups(t) for t in tokens(term, aliases)))
            matches = {}
            for shard in sorted({shard_for(g) for g in groups}):
                if shard not in manifest["lexical_shards"]:
                    continue
                contents = self.read_compressed(f"{manifest['prefix']}/lexical/{shard}.json.gz")
                for group in groups:
                    for token, rows in contents.get(group, {}).items():
                        if not matcher(token, term):
                            continue
                        for cid, sid, tf, length in rows:
                            # A token may occur in several substring groups.
                            matches.setdefault(cid, {"sid": sid, "length": length, "tokens": {}})["tokens"][token] = tf
            df = len(matches)
            if not df:
                continue
            idf = math.log(1 + (manifest["chunk_count"] - df + 0.5) / (df + 0.5))
            for cid, match in matches.items():
                tf = sum(match["tokens"].values())
                denominator = tf + 1.35 * (1 - 0.72 + 0.72 * match["length"] / manifest["avgdl"])
                scores[(cid, match["sid"])] += idf * tf * 2.35 / denominator
        return [(cid, sid, score) for (cid, sid), score in scores.most_common(limit)]

    def semantic_candidates(self, manifest, embedding, limit=100):
        result = self.vectors.query_vectors(indexArn=manifest["index_arn"], topK=limit,
            queryVector={"float32": embedding}, filter={"snapshot": manifest["snapshot"]},
            returnMetadata=True, returnDistance=True)
        return [(v["metadata"]["chunk_id"], v["metadata"]["sermon_id"],
                 max(0.0, 1 - v["distance"])) for v in result.get("vectors", [])]

    def hydrate(self, manifest, sermon_ids):
        entries = {s["sermon_id"]: s for s in manifest["sermons"]}
        def load(sid):
            if sid not in entries:
                raise ArchiveUnavailable(f"Missing sermon metadata: {sid}")
            chunks = self.read_compressed(f"{manifest['prefix']}/sermons/{sid}.json.gz")
            return {**entries[sid], "chunks": chunks}
        with ThreadPoolExecutor(max_workers=8) as pool:
            return list(pool.map(load, sorted(set(sermon_ids))))
