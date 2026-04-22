"""
Pulpit — Query Lambda v2

Semantic search over the full sermon archive using Titan Embeddings.
Zero baseline cost — no OpenSearch, no Pinecone, no vector DB.

How it works:
1. Check DynamoDB cache — identical questions return instantly
2. Load pre-computed embedding index from S3 (one GET, cached in Lambda global)
3. Embed the question via Titan Embed Text v2
4. Cosine similarity in pure Python → top 5 most relevant sermons
5. Nova Lite generates a cited answer from those 5 sermons
6. Cache result in DynamoDB (30-day TTL)

Why index.json instead of N individual S3 GETs:
- API Gateway hard-cuts at 29s. Loading 100 files × ~80ms = 8s just for S3.
- Single index.json loads in ~200ms regardless of archive size.
- Lambda global caches it across warm invocations — subsequent calls are instant.

Scales to ~500 sermons before the index file becomes unwieldy (~5MB).
"""

import json
import os
import uuid
import math
import hashlib
import boto3
from datetime import datetime, timezone

s3       = boto3.client("s3")
bedrock  = boto3.client("bedrock-runtime")
dynamodb = boto3.resource("dynamodb")

MODEL_ID       = os.environ["BEDROCK_MODEL_ID"]
EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
BUCKET         = os.environ["TRANSCRIPT_BUCKET"]
GUARDRAIL_ID   = os.environ["GUARDRAIL_ID"]
GUARDRAIL_VER  = os.environ["GUARDRAIL_VERSION"]
LOG_TABLE      = os.environ["DYNAMODB_TABLE"]
CACHE_TABLE    = os.environ["CACHE_TABLE"]
PASTOR_CONTACT = os.environ["PASTOR_CONTACT"]
ENVIRONMENT    = os.environ["ENVIRONMENT"]

TOP_K           = 5     # sermons sent to Nova Lite
FALLBACK_LIMIT  = 30    # max sermons if index has no embeddings yet
CACHE_TTL_DAYS  = 30
INDEX_TTL_SEC   = 600   # reload index every 10 min to pick up new sermons

CRISIS_KEYWORDS = [
    "suicide", "kill myself", "self harm", "abuse", "hurt myself",
    "don't want to live", "end my life", "hurting me"
]

SYSTEM_PROMPT = """You are Pulpit, a sermon research assistant for Atlanta Bethel Church.

You are given the most relevant sermon excerpts from the archive, retrieved by semantic search.

Rules you must always follow:
1. Only answer based on the sermon content actually provided — never fabricate.
2. Cite every claim: [Sermon Title — Date]
3. List every relevant sermon you find in the provided excerpts.
4. If none of the provided sermons address the question, say so clearly.
5. Never give personal spiritual advice beyond what was taught from the pulpit.
6. Respond with warmth — you are serving a faith community."""

# ── Lambda-global index cache (survives warm invocations) ──────────────────
_sermon_index    = None   # list of index entries from index.json
_index_loaded_at = None


def lambda_handler(event, context):
    try:
        body        = json.loads(event.get("body", "{}"))
        question    = body.get("question", "").strip()
        claims      = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
        user_id     = claims.get("sub", "anonymous")
        user_groups = claims.get("cognito:groups", "member")

        if not question:
            return response(400, {"error": "Question is required."})

        # Crisis detection — redirect before hitting Bedrock
        if is_crisis_disclosure(question):
            return response(200, {
                "answer": (
                    "It sounds like you may be going through something difficult. "
                    "Please reach out to our pastoral team directly — they are here for you. "
                    f"{PASTOR_CONTACT}"
                ),
                "crisis_redirect": True
            })

        # 1. Cache check — identical questions cost nothing
        cached = check_cache(question)
        if cached:
            return response(200, {**cached, "cached": True})

        # 2. Semantic search across full archive
        sermons = find_relevant_sermons(question)
        if not sermons:
            return response(200, {
                "answer": (
                    "No sermons in the archive yet. "
                    "Check back after the next ingestion run."
                )
            })

        # 3. Generate answer
        sermon_context = build_sermon_context(sermons)
        prompt         = f"{sermon_context}\n\nQuestion: {question}"
        answer         = invoke_bedrock(prompt)

        # 4. Cache + audit log
        sources = [
            {
                "title":       e.get("title", ""),
                "date":        e.get("date", ""),
                "youtube_url": e.get("youtube_url", ""),
            }
            for e in sermons
        ]
        result = {"answer": answer, "sermons_searched": len(sermons), "sources": sources}
        cache_answer(question, result)
        log_query(user_id, user_groups, question, answer)

        return response(200, result)

    except Exception as e:
        print(f"Error: {e}")
        import traceback; traceback.print_exc()
        return response(500, {"error": "Something went wrong. Please try again."})


# ── SEMANTIC SEARCH ────────────────────────────────────────────────────────

def find_relevant_sermons(question):
    """Embed question, cosine-rank all sermons, return top K."""
    index = get_sermon_index()
    if not index:
        return []

    entries_with_embeddings = [e for e in index if e.get("embedding")]

    if entries_with_embeddings:
        q_vec = embed_text(question)
        if q_vec:
            ranked = sorted(
                entries_with_embeddings,
                key=lambda e: cosine_similarity(q_vec, e["embedding"]),
                reverse=True
            )
            top = ranked[:TOP_K]
            scores = [cosine_similarity(q_vec, e["embedding"]) for e in top]
            print(f"Top {TOP_K} similarity scores: {[f'{s:.3f}' for s in scores]}")
            return [e for e in top]  # return full index entries (have transcript)

    # Fallback: no embeddings yet — return most recent sermons
    print(f"No embeddings in index — falling back to {FALLBACK_LIMIT} most recent sermons")
    all_entries = sorted(index, key=lambda e: e.get("date", ""), reverse=True)
    return all_entries[:FALLBACK_LIMIT]


def get_sermon_index():
    """Load index.json with Lambda-global caching."""
    global _sermon_index, _index_loaded_at
    now = datetime.now(timezone.utc)

    if _sermon_index is not None and _index_loaded_at:
        age = (now - _index_loaded_at).total_seconds()
        if age < INDEX_TTL_SEC:
            return _sermon_index

    print("Loading sermon index from S3...")
    try:
        raw    = s3.get_object(Bucket=BUCKET, Key="transcripts/index.json")
        data   = json.loads(raw["Body"].read())
        _sermon_index    = data.get("sermons", [])
        _index_loaded_at = now
        print(f"Loaded index: {len(_sermon_index)} sermons, "
              f"generated {data.get('generated_at', 'unknown')}")
        return _sermon_index
    except s3.exceptions.NoSuchKey:
        print("No index.json found — run ingest script to build it")
        return []
    except Exception as e:
        print(f"Error loading index: {e}")
        return []


def embed_text(text):
    """Generate 256-dim embedding via Titan Embed Text v2."""
    try:
        resp = bedrock.invoke_model(
            modelId=EMBED_MODEL_ID,
            body=json.dumps({
                "inputText":  text[:8000],  # Titan max input
                "dimensions": 256,
                "normalize":  True
            })
        )
        return json.loads(resp["body"].read())["embedding"]
    except Exception as e:
        print(f"Embedding error: {e}")
        return None


def cosine_similarity(a, b):
    """Pure-Python cosine similarity — no numpy needed in Lambda."""
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ── CONTEXT BUILDING ────────────────────────────────────────────────────────

def build_sermon_context(entries):
    """Format top-K sermon excerpts for Nova Lite's context window."""
    lines = ["Relevant sermon excerpts from the archive (retrieved by semantic search):\n"]

    for i, entry in enumerate(entries, 1):
        title      = entry.get("title", "Unknown")
        date       = entry.get("date", "Unknown date")
        pastor     = entry.get("pastor_name", "")
        topics     = ", ".join(entry.get("topics", []))
        scripture  = ", ".join(entry.get("scripture_references", []))
        transcript = entry.get("transcript", "")[:2000]

        lines.append(f"--- SERMON {i} ---")
        lines.append(f"Title: {title}")
        lines.append(f"Date: {date}")
        if pastor:    lines.append(f"Pastor: {pastor}")
        if topics:    lines.append(f"Topics: {topics}")
        if scripture: lines.append(f"Scripture: {scripture}")
        lines.append(f"Transcript:\n{transcript}\n")

    return "\n".join(lines)


# ── BEDROCK ────────────────────────────────────────────────────────────────

def invoke_bedrock(prompt):
    resp = bedrock.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 2000},
        guardrailConfig={
            "guardrailIdentifier": GUARDRAIL_ID,
            "guardrailVersion":    GUARDRAIL_VER,
            "trace":               "disabled"
        }
    )
    return resp["output"]["message"]["content"][0]["text"]


# ── CACHE ──────────────────────────────────────────────────────────────────

def question_hash(question):
    return hashlib.sha256(question.lower().strip().encode()).hexdigest()


def check_cache(question):
    try:
        table = dynamodb.Table(CACHE_TABLE)
        item  = table.get_item(Key={"questionHash": question_hash(question)}).get("Item")
        if item:
            print("Cache hit")
            return {
                "answer":           item["answer"],
                "sermons_searched": int(item.get("sermons_searched", 0)),
                "sources":          item.get("sources", [])
            }
    except Exception as e:
        print(f"Cache read error: {e}")
    return None


def cache_answer(question, result):
    try:
        table = dynamodb.Table(CACHE_TABLE)
        now   = datetime.now(timezone.utc)
        table.put_item(Item={
            "questionHash":     question_hash(question),
            "question":         question,
            "answer":           result["answer"],
            "sermons_searched": result.get("sermons_searched", 0),
            "sources":          result.get("sources", []),
            "cachedAt":         now.isoformat(),
            "expiresAt":        int(now.timestamp()) + (CACHE_TTL_DAYS * 86400)
        })
    except Exception as e:
        print(f"Cache write error: {e}")


# ── AUDIT LOG ──────────────────────────────────────────────────────────────

def log_query(user_id, user_groups, question, answer):
    try:
        table    = dynamodb.Table(LOG_TABLE)
        now      = datetime.now(timezone.utc)
        ttl_days = 90 if ENVIRONMENT == "dev" else 365
        table.put_item(Item={
            "queryId":   str(uuid.uuid4()),
            "timestamp": now.isoformat(),
            "userId":    user_id,
            "userGroup": user_groups,
            "question":  question,
            "answer":    answer,
            "expiresAt": str(int(now.timestamp()) + (ttl_days * 86400))
        })
    except Exception as e:
        print(f"Log error: {e}")


# ── UTILS ──────────────────────────────────────────────────────────────────

def is_crisis_disclosure(text):
    t = text.lower()
    return any(kw in t for kw in CRISIS_KEYWORDS)


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type":                 "application/json",
            "Access-Control-Allow-Origin":  "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "POST,OPTIONS"
        },
        "body": json.dumps(body, ensure_ascii=False)
    }
