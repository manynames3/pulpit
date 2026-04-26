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
import re
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
LEAD_PASTOR    = os.environ.get("LEAD_PASTOR_NAME", "이혜진 목사")

TOP_K           = 5     # sermons sent to Nova Lite
FALLBACK_LIMIT  = 30    # max sermons if index has no embeddings yet
CACHE_TTL_DAYS  = 30
INDEX_TTL_SEC   = 600   # reload index every 10 min to pick up new sermons
MIN_RELEVANCE_SCORE = 0.35
EXPANDED_RELEVANCE_SCORE = 0.30

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
        http_method = event.get("httpMethod", "")
        resource    = event.get("resource", "")

        if http_method == "GET" and resource == "/catalog":
            return response(200, build_catalog_response())

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
                    "I could not find a sermon in the archive that clearly addresses that topic. "
                    "Try a broader keyword, a Bible passage, or a more specific sermon question."
                ),
                "sources": []
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
        ranked = rank_sermons(entries_with_embeddings, [question])
        if ranked:
            keyword_ranked = rerank_keyword_matches(ranked, question)
            if keyword_ranked:
                keyword_top = keyword_ranked[:TOP_K]
                keyword_scores = [score for _, score in keyword_top]
                print(f"Keyword-ranked top {TOP_K} scores: {[f'{s:.3f}' for s in keyword_scores]}")
                return [entry for entry, _ in keyword_top]

            top = ranked[:TOP_K]
            scores = [score for _, score in top]
            print(f"Top {TOP_K} similarity scores: {[f'{s:.3f}' for s in scores]}")
            if scores[0] >= MIN_RELEVANCE_SCORE:
                return [entry for entry, _ in top]

            variants = filter_query_variants(index, expand_query_variants(question))
            if variants:
                print(f"Retrying retrieval with variants: {variants}")
                expanded_ranked = rank_sermons(entries_with_embeddings, variants)
                if expanded_ranked:
                    expanded_top = expanded_ranked[:TOP_K]
                    expanded_scores = [score for _, score in expanded_top]
                    print(f"Expanded top {TOP_K} scores: {[f'{s:.3f}' for s in expanded_scores]}")
                    if expanded_scores[0] >= EXPANDED_RELEVANCE_SCORE:
                        return [entry for entry, _ in expanded_top]

            print(f"Top score {scores[0] if scores else 'n/a'} below thresholds {MIN_RELEVANCE_SCORE}/{EXPANDED_RELEVANCE_SCORE}")
            return []

    # Fallback: no embeddings yet — return most recent sermons
    print(f"No embeddings in index — falling back to {FALLBACK_LIMIT} most recent sermons")
    all_entries = sorted(index, key=lambda e: e.get("date", ""), reverse=True)
    return all_entries[:FALLBACK_LIMIT]


def pastor_priority(entry):
    pastor = (entry.get("pastor_name") or "").strip()
    if not pastor:
        return 0
    return int(LEAD_PASTOR in pastor or pastor in LEAD_PASTOR)


def rank_sermons(entries_with_embeddings, queries):
    vectors = []
    for query in queries:
        vec = embed_text(query)
        if vec:
            vectors.append(vec)

    if not vectors:
        return []

    scored = []
    for entry in entries_with_embeddings:
        score = max(cosine_similarity(vec, entry["embedding"]) for vec in vectors)
        scored.append((entry, score))

    return sorted(
        scored,
        key=lambda item: (
            pastor_priority(item[0]),
            item[1]
        ),
        reverse=True
    )


def rerank_keyword_matches(ranked_sermons, question):
    if not is_literal_keyword_query(question):
        return []

    terms = extract_literal_terms(question)
    if not terms:
        return []

    matched = []
    for entry, semantic_score in ranked_sermons:
        lexical_score = lexical_match_score(entry, terms)
        if lexical_score <= 0:
            continue
        matched.append((entry, semantic_score, lexical_score))

    if not matched:
        print(f"No literal keyword matches for query '{question}'")
        return []

    matched.sort(
        key=lambda item: (
            pastor_priority(item[0]),
            item[2],
            item[1]
        ),
        reverse=True
    )
    print(f"Literal keyword rerank applied for '{question}' with {len(matched)} matches")
    return [(entry, semantic_score) for entry, semantic_score, _ in matched]


def is_literal_keyword_query(question):
    tokens = [token for token in re.split(r"\s+", question.strip()) if token]
    if not tokens or len(tokens) > 3 or len(question.strip()) > 20:
        return False

    hangul_or_word = re.findall(r"[가-힣A-Za-z0-9]+", question)
    if not hangul_or_word:
        return False

    return "".join(hangul_or_word) == re.sub(r"\s+", "", question.strip())


def extract_literal_terms(question):
    terms = []
    seen = set()

    for token in re.findall(r"[가-힣A-Za-z0-9]+", question.lower()):
        cleaned = token.strip()
        if len(cleaned) < minimum_term_length(cleaned) or cleaned in seen:
            continue
        seen.add(cleaned)
        terms.append(cleaned)

    return terms


def lexical_match_score(entry, terms):
    title = (entry.get("title") or "").lower()
    topics = " ".join(entry.get("topics", [])).lower()
    scripture = " ".join(entry.get("scripture_references", [])).lower()
    description = (entry.get("description") or "").lower()
    transcript = (entry.get("transcript") or "").lower()

    score = 0
    for term in terms:
        title_hits = title.count(term)
        topic_hits = topics.count(term)
        scripture_hits = scripture.count(term)
        description_hits = description.count(term)
        transcript_hits = transcript.count(term)

        if title_hits:
            score += 12 + min(title_hits, 3)
        if topic_hits:
            score += 10 + min(topic_hits, 3)
        if scripture_hits:
            score += 8 + min(scripture_hits, 2)
        if description_hits:
            score += 6 + min(description_hits, 2)
        if transcript_hits:
            score += min(transcript_hits, 12)

    return score


def minimum_term_length(term):
    return 1 if re.fullmatch(r"[가-힣]", term) else 2


def expand_query_variants(question):
    if not should_expand_query(question):
        return []

    prompt = (
        "Rewrite this church sermon archive search into up to 4 short search variants for a Korean sermon archive. "
        "Prefer standard Korean Bible and church vocabulary over casual synonyms. "
        "Include Korean equivalents, Bible names, and likely sermon keywords when helpful. "
        "Return one variant per line only. No bullets. No explanations.\n\n"
        f"Query: {question}"
    )

    try:
        resp = bedrock.converse(
            modelId=MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 120, "temperature": 0.1}
        )
        raw = resp["output"]["message"]["content"][0]["text"]
        variants = []
        seen = {question.strip().lower()}

        for line in raw.splitlines():
            candidate = re.sub(r"^\s*(?:[-*\d.)]+)\s*", "", line).strip()
            if not candidate or len(candidate) > 80:
                continue
            key = candidate.lower()
            if key in seen:
                continue
            seen.add(key)
            variants.append(candidate)

        return variants[:4]
    except Exception as e:
        print(f"Query expansion error: {e}")
        return []


def should_expand_query(question):
    q = question.strip()
    if not q or len(q) > 120:
        return False
    return any("a" <= ch.lower() <= "z" for ch in q)


def filter_query_variants(index, variants):
    filtered = []
    for variant in variants:
        core = normalize_variant(variant)
        if not core:
            continue
        if archive_contains_term(index, core):
            filtered.append(core)
    return filtered


def normalize_variant(variant):
    text = variant.strip()
    text = re.sub(r"\b(?:search|sermon|bible|topic|story|archive)\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(검색|설교|성경|주제|이야기|아카이브)", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -,:")
    return text.strip()


def archive_contains_term(index, term):
    needle = term.lower().strip()
    if len(needle) < minimum_term_length(needle):
        return False

    for entry in index:
        hay = " ".join([
            entry.get("title", ""),
            " ".join(entry.get("topics", [])),
            " ".join(entry.get("scripture_references", [])),
            entry.get("transcript", "")[:4000]
        ]).lower()
        if needle in hay:
            return True
    return False


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


def build_catalog_response():
    index = get_sermon_index()
    sermons = sorted(index, key=lambda e: e.get("date", ""), reverse=True)

    return {
        "sermon_count": len(sermons),
        "sermons": [
            {
                "sermon_id":            entry.get("sermon_id", ""),
                "title":                entry.get("title", ""),
                "date":                 entry.get("date", ""),
                "youtube_url":          entry.get("youtube_url", ""),
                "pastor_name":          entry.get("pastor_name", ""),
                "description":          entry.get("description", ""),
                "topics":               entry.get("topics", []),
                "scripture_references": entry.get("scripture_references", [])
            }
            for entry in sermons
        ]
    }


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
            if "sources" not in item:
                print("Cache miss: legacy entry missing sources")
                return None
            answer_text = item.get("answer", "").lower()
            sources = item.get("sources", [])
            if sources and (
                "none of the provided sermons address" in answer_text or
                "does not mention a" in answer_text
            ):
                print("Cache miss: legacy weak-match answer")
                return None
            print("Cache hit")
            return {
                "answer":           item["answer"],
                "sermons_searched": int(item.get("sermons_searched", 0)),
                "sources":          sources
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
