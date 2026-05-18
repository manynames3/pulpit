"""
Pulpit — Query Lambda v3

Chunked hybrid search over the sermon archive using a prebuilt S3 index.
Zero baseline cost — no OpenSearch, no Pinecone, no vector database.

How it works:
1. Check DynamoDB cache — identical questions return instantly
2. Load pre-computed chunked index from S3 (one GET, cached in Lambda global)
3. Embed the question via Titan Embed Text v2
4. Hybrid rank transcript chunks with semantic + lexical signals
5. Collapse the best chunks back to sermons
6. Bedrock generates a cited answer from the matched sermon excerpts
7. Cache the result for repeat questions
"""

import json
import os
import uuid
import math
import hashlib
import re
import boto3
from collections import Counter
from datetime import datetime, timezone

from botocore.exceptions import ClientError

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
CONFIG_TABLE   = os.environ.get("CONFIG_TABLE")
EVAL_TABLE     = os.environ.get("EVAL_TABLE")
PASTOR_CONTACT = os.environ["PASTOR_CONTACT"]
ENVIRONMENT    = os.environ["ENVIRONMENT"]
LEAD_PASTOR    = os.environ.get("LEAD_PASTOR_NAME", "이혜진 목사")

TOP_K           = 5     # sermons sent to the Bedrock answer model
CHUNK_TOP_K     = 12    # candidate chunk hits before collapsing to sermons
FALLBACK_LIMIT  = 30    # max sermons if index has no embeddings yet
CACHE_TTL_DAYS  = 30
INDEX_TTL_SEC   = 600   # reload index every 10 min to pick up new sermons
MIN_RELEVANCE_SCORE = 0.35
EXPANDED_RELEVANCE_SCORE = 0.30
MIN_HYBRID_SCORE = 0.28
MIN_CHUNK_SEMANTIC_SCORE = 0.22
RETRIEVAL_VERSION = "v11-korean-topic-synonym-rerank"
TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
ASCII_TERM_RE = re.compile(r"^[a-z0-9]+$")
HANGUL_RE = re.compile(r"[가-힣]")

ENGLISH_LEXICAL_ALIASES = {
    "fail": "fail",
    "fails": "fail",
    "failed": "fail",
    "failing": "fail",
    "failure": "fail",
    "failures": "fail",
}

DEFAULT_RETRIEVAL_CONFIG = {
    "configKey": "retrieval",
    "version": "default",
    "preferredSermons": [],
    "hiddenSermons": [],
}

STATIC_QUERY_VARIANTS = {
    "wilderness": ["광야"],
    "noah": ["노아"],
    "ark": ["방주", "노아", "홍수"],
    "noahs ark": ["노아", "방주", "홍수"],
    "noah's ark": ["노아", "방주", "홍수"],
    "flood": ["홍수", "노아"],
    "flooding": ["홍수", "노아"],
    "pillar of cloud": ["구름기둥"],
    "cloud pillar": ["구름기둥"],
    "pillar of fire": ["불기둥"],
    "fire column": ["불기둥"],
    "peter": ["베드로"],
    "apostle peter": ["베드로"],
    "genesis": ["창세기"],
    "jacob": ["야곱"],
    "moses": ["모세"],
    "exodus": ["출애굽"],
}

KOREAN_STOP_TERMS = {
    "관련", "관한", "교회", "내용", "대한", "대해", "대해서", "목사",
    "목사님", "설교", "설교가", "설교를", "설교에서", "설교한", "있나",
    "있나요", "있는", "있을까요", "최근", "최근에", "찾아", "찾아줘",
    "했나", "했나요", "하셨나", "하셨나요",
}

KOREAN_PARTICLE_SUFFIXES = (
    "으로부터", "으로써", "으로서", "에게서", "한테서", "께서는", "에서는",
    "이라고", "라고", "에서", "에게", "한테", "부터", "까지", "처럼",
    "보다", "만큼", "마다", "으로", "이라", "라", "이나", "나", "은",
    "는", "이", "가", "을", "를", "에", "의", "도", "만", "와",
    "과", "로",
)

RECENT_QUERY_TERMS = ("최근", "최근에", "요즘", "latest", "recent", "recently")

KOREAN_LEXICAL_ALIASES = {
    "자만": ["교만", "오만"],
    "교만": ["자만", "오만"],
    "오만": ["자만", "교만"],
}

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
_sermon_index        = None   # list of index entries from index.json
_index_loaded_at     = None
_index_generated_at  = ""
_retrieval_config = None
_retrieval_config_loaded_at = None


def lambda_handler(event, context):
    try:
        http_method = event.get("httpMethod", "")
        resource    = event.get("resource", "")

        if http_method == "GET" and resource == "/catalog":
            return response(200, build_catalog_response())

        body        = json.loads(event.get("body", "{}"))
        question    = body.get("question", "").strip()
        preferred_language = normalize_language(body.get("preferredLanguage"))
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

        retrieval_config = get_retrieval_config()

        # 1. Cache check — identical questions cost nothing
        cached = check_cache(question, preferred_language, retrieval_config)
        if cached:
            return response(200, {**cached, "cached": True})

        # 2. Semantic search across full archive
        sermons = find_relevant_sermons(question, retrieval_config)
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
        answer         = invoke_bedrock(prompt, preferred_language)

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
        cache_answer(question, result, preferred_language, retrieval_config)
        log_query(user_id, user_groups, question, answer)
        log_retrieval_eval(user_id, question, answer, sermons, retrieval_config)

        return response(200, result)

    except Exception as e:
        print(f"Error: {e}")
        import traceback; traceback.print_exc()
        return response(500, {"error": "Something went wrong. Please try again."})


# ── SEMANTIC SEARCH ────────────────────────────────────────────────────────

def find_relevant_sermons(question, retrieval_config=None):
    """Rank archive results and return top sermons with matched excerpts."""
    retrieval_config = retrieval_config or DEFAULT_RETRIEVAL_CONFIG
    index = filter_hidden_sermons(get_sermon_index(), retrieval_config)
    if not index:
        return []

    if any(entry.get("chunks") for entry in index):
        chunk_results = find_relevant_sermons_from_chunks(index, question, retrieval_config)
        if chunk_results:
            return chunk_results

    entries_with_embeddings = [e for e in index if e.get("embedding")]
    return find_relevant_sermons_by_sermon_embedding(index, entries_with_embeddings, question, retrieval_config)


def find_relevant_sermons_by_sermon_embedding(index, entries_with_embeddings, question, retrieval_config=None):
    if entries_with_embeddings:
        ranked = rank_sermons(entries_with_embeddings, [question], retrieval_config)
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
                expanded_ranked = rank_sermons(entries_with_embeddings, variants, retrieval_config)
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


def find_relevant_sermons_from_chunks(index, question, retrieval_config=None):
    query_variants = build_query_bundle(index, question)
    chunk_hits = rank_chunk_hits(index, query_variants, question, retrieval_config)
    if not chunk_hits:
        return []

    if is_literal_keyword_query(question):
        lexical_hits = [hit for hit in chunk_hits if hit["lexical_score"] > 0]
        if lexical_hits:
            chunk_hits = lexical_hits
        else:
            print(f"No literal chunk mentions found for exact query '{question}'")
            return []

    best = chunk_hits[0]
    print(
        f"Top chunk scores — combined={best['combined_score']:.3f}, "
        f"semantic={best['semantic_score']:.3f}, lexical={best['lexical_score']}"
    )
    if (
        best["combined_score"] < MIN_HYBRID_SCORE
        and best["semantic_score"] < MIN_CHUNK_SEMANTIC_SCORE
        and best["lexical_score"] == 0
    ):
        print("Chunk retrieval below hybrid thresholds")
        return []

    return collapse_chunk_hits_to_sermons(chunk_hits, retrieval_config)


def pastor_priority(entry):
    pastor = (entry.get("pastor_name") or "").strip()
    if not pastor:
        return 0
    return int(LEAD_PASTOR in pastor or pastor in LEAD_PASTOR)


def configured_priority(entry, retrieval_config=None):
    preferred = set((retrieval_config or {}).get("preferredSermons") or [])
    if not preferred:
        return 0
    return int(
        entry.get("sermon_id") in preferred
        or entry.get("title") in preferred
        or entry.get("youtube_url") in preferred
    )


def rank_sermons(entries_with_embeddings, queries, retrieval_config=None):
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
            configured_priority(item[0], retrieval_config),
            pastor_priority(item[0]),
            item[1]
        ),
        reverse=True
    )


def build_query_bundle(index, question):
    variants = []
    seen = {question.strip().lower()}

    for candidate in static_query_variants(question) + expand_query_variants(question):
        core = normalize_variant(candidate)
        if not core:
            continue
        key = core.lower()
        if key in seen:
            continue
        seen.add(key)
        variants.append(core)

    filtered = [variant for variant in variants if archive_contains_term(index, variant)]
    return [question] + filtered


def static_query_variants(question):
    normalized = re.sub(r"\s+", " ", question.strip().lower())
    normalized = normalized.replace("’", "'")
    matches = []
    for key, values in STATIC_QUERY_VARIANTS.items():
        if normalized == key or key in normalized:
            matches.extend(values)
    return matches


def rank_chunk_hits(index, queries, question, retrieval_config=None):
    vectors = []
    for query in queries:
        vec = embed_text(query)
        if vec:
            vectors.append(vec)

    terms = collect_search_terms([question] + queries)
    if not vectors and not terms:
        return []
    hits = []

    for entry in index:
        for chunk in entry.get("chunks", []):
            lexical_score = lexical_match_score(entry, terms, chunk.get("text", ""))
            embedding = chunk.get("embedding")
            semantic_score = max(cosine_similarity(vec, embedding) for vec in vectors) if embedding and vectors else 0.0
            combined_score = semantic_score + lexical_bonus(lexical_score)

            if semantic_score <= 0 and lexical_score <= 0:
                continue

            hits.append({
                "entry": entry,
                "chunk": chunk,
                "semantic_score": semantic_score,
                "lexical_score": lexical_score,
                "combined_score": combined_score,
            })

    hits.sort(
        key=lambda item: (
            configured_priority(item["entry"], retrieval_config),
            pastor_priority(item["entry"]),
            item["combined_score"],
            item["lexical_score"],
            item["semantic_score"],
        ),
        reverse=True,
    )
    return hits[:CHUNK_TOP_K]


def collapse_chunk_hits_to_sermons(chunk_hits, retrieval_config=None):
    sermons = {}

    for hit in chunk_hits:
        entry = hit["entry"]
        sermon_id = entry.get("sermon_id")
        if sermon_id not in sermons:
            sermons[sermon_id] = {
                **entry,
                "match_score": hit["combined_score"],
                "matched_chunks": [],
            }

        sermon = sermons[sermon_id]
        sermon["match_score"] = max(sermon["match_score"], hit["combined_score"])
        sermon["matched_chunks"].append({
            "text": hit["chunk"].get("text", ""),
            "semantic_score": hit["semantic_score"],
            "lexical_score": hit["lexical_score"],
            "combined_score": hit["combined_score"],
        })

    results = []
    for sermon in sermons.values():
        sermon["matched_chunks"] = sorted(
            sermon["matched_chunks"],
            key=lambda chunk: (chunk["combined_score"], chunk["lexical_score"], chunk["semantic_score"]),
            reverse=True,
        )[:2]
        results.append(sermon)

    results.sort(
        key=lambda item: (
            configured_priority(item, retrieval_config),
            pastor_priority(item),
            item.get("match_score", 0),
            item.get("date", ""),
        ),
        reverse=True,
    )
    return results[:TOP_K]


def rerank_keyword_matches(ranked_sermons, question):
    if not should_keyword_rerank(question):
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

    if wants_recent_results(question):
        matched.sort(
            key=lambda item: (
                pastor_priority(item[0]),
                item[0].get("date", ""),
                item[2],
                item[1],
            ),
            reverse=True,
        )
    else:
        matched.sort(
            key=lambda item: (
                pastor_priority(item[0]),
                item[2],
                item[1],
            ),
            reverse=True,
        )

    print(f"Keyword rerank applied for '{question}' with {len(matched)} matches and terms {terms}")
    return [(entry, semantic_score) for entry, semantic_score, _ in matched]


def should_keyword_rerank(question):
    if is_literal_keyword_query(question):
        return True

    terms = extract_literal_terms(question)
    if not terms:
        return False

    # Natural-language Korean topic questions often include particles:
    # "자만에 대한 설교" should still boost exact "자만" archive mentions.
    return any(HANGUL_RE.search(term) for term in terms) and len(terms) <= 5


def wants_recent_results(question):
    normalized = question.lower()
    return any(term in normalized for term in RECENT_QUERY_TERMS)


def is_literal_keyword_query(question):
    tokens = [token for token in re.split(r"\s+", question.strip()) if token]
    if not tokens or len(tokens) > 3 or len(question.strip()) > 20:
        return False

    hangul_or_word = re.findall(r"[가-힣A-Za-z0-9]+", question)
    if not hangul_or_word:
        return False

    compact_question = re.sub(r"[\s'’]", "", question.strip())
    return "".join(hangul_or_word) == compact_question


def extract_literal_terms(question):
    terms = []
    seen = set()

    for token in re.findall(r"[가-힣A-Za-z0-9]+", question.lower()):
        cleaned = token.strip()
        if len(cleaned) < minimum_term_length(cleaned):
            continue

        if HANGUL_RE.search(cleaned):
            candidates = normalize_korean_lexical_terms(cleaned)
        else:
            candidates = [normalize_english_lexical_token(cleaned)]

        for term in candidates:
            if term in seen:
                continue
            seen.add(term)
            terms.append(term)

    return terms


def normalize_korean_lexical_terms(token):
    base = strip_korean_suffix(token)
    candidate = base if base != token else token

    if candidate in KOREAN_STOP_TERMS:
        return []
    if len(candidate) < minimum_term_length(candidate):
        return []

    terms = [candidate]
    for alias in KOREAN_LEXICAL_ALIASES.get(candidate, []):
        if alias not in terms:
            terms.append(alias)
    return terms


def strip_korean_suffix(token):
    for suffix in KOREAN_PARTICLE_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            return token[:-len(suffix)]
    return token


def collect_search_terms(queries):
    terms = []
    seen = set()

    for query in queries:
        for term in extract_literal_terms(query):
            if term in seen:
                continue
            seen.add(term)
            terms.append(term)

    return terms


def lexical_match_score(entry, terms, transcript_text=None):
    title = (entry.get("title") or "").lower()
    topics = " ".join(entry.get("topics", [])).lower()
    scripture = " ".join(entry.get("scripture_references", [])).lower()
    description = (entry.get("description") or "").lower()
    transcript = (transcript_text if transcript_text is not None else entry.get("transcript", "")).lower()

    score = 0
    for term in terms:
        title_hits = term_count(title, term)
        topic_hits = term_count(topics, term)
        scripture_hits = term_count(scripture, term)
        description_hits = term_count(description, term)
        transcript_hits = term_count(transcript, term)

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


def tokenize(text):
    return [
        normalize_english_lexical_token(token.lower())
        for token in TOKEN_RE.findall(text or "")
        if len(token) >= minimum_term_length(token)
    ]


def term_count(text, term):
    """Use exact token matches for English; keep substring matching for Korean."""
    normalized = (term or "").lower().strip()
    if not normalized:
        return 0
    if ASCII_TERM_RE.fullmatch(normalized):
        lexical_term = normalize_english_lexical_token(normalized)
        return Counter(tokenize(text)).get(lexical_term, 0)
    return (text or "").lower().count(normalized)


def normalize_english_lexical_token(token):
    """Collapse common English word forms so exact lexical scoring is less brittle."""
    if not ASCII_TERM_RE.fullmatch(token or ""):
        return token

    if token in ENGLISH_LEXICAL_ALIASES:
        return ENGLISH_LEXICAL_ALIASES[token]

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


def lexical_bonus(score):
    if score <= 0:
        return 0.0
    return min(score / 24.0, 0.75)


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
        chunk_text = " ".join(chunk.get("text", "") for chunk in entry.get("chunks", []))
        hay = " ".join([
            entry.get("title", ""),
            " ".join(entry.get("topics", [])),
            " ".join(entry.get("key_themes", [])),
            " ".join(entry.get("scripture_references", [])),
            entry.get("description", ""),
            chunk_text,
            entry.get("transcript", "")[:4000]
        ]).lower()
        if needle in hay:
            return True
    return False


def get_sermon_index():
    """Load index.json with Lambda-global caching."""
    global _sermon_index, _index_loaded_at, _index_generated_at
    now = datetime.now(timezone.utc)

    if _sermon_index is not None and _index_loaded_at:
        age = (now - _index_loaded_at).total_seconds()
        if age < INDEX_TTL_SEC:
            return _sermon_index

    print("Loading sermon index from S3...")
    try:
        raw    = s3.get_object(Bucket=BUCKET, Key="transcripts/index.json")
        data   = json.loads(raw["Body"].read())
        _sermon_index    = merge_external_chunk_index(data.get("sermons", []))
        _index_loaded_at = now
        _index_generated_at = data.get("generated_at", "")
        print(f"Loaded index: {len(_sermon_index)} sermons, "
              f"generated {data.get('generated_at', 'unknown')}")
        return _sermon_index
    except s3.exceptions.NoSuchKey:
        print("No index.json found — run ingest script to build it")
        return []
    except Exception as e:
        print(f"Error loading index: {e}")
        return []


def merge_external_chunk_index(sermons):
    """
    Preserve compatibility with both index shapes:
    - transcripts/index.json with inline sermon chunks
    - indexes/chunk-index.json written by the serverless ingest pipeline
    """
    chunk_payload = load_optional_json("indexes/chunk-index.json")
    chunks = chunk_payload.get("chunks", []) if isinstance(chunk_payload, dict) else []
    if not chunks:
        return sermons

    chunks_by_sermon = {}
    for chunk in chunks:
        sermon_id = chunk.get("sermon_id")
        if sermon_id:
            chunks_by_sermon.setdefault(sermon_id, []).append(chunk)

    if not chunks_by_sermon:
        return sermons

    merged = []
    seen_sermon_ids = set()
    for entry in sermons:
        sermon_id = entry.get("sermon_id")
        seen_sermon_ids.add(sermon_id)
        external_chunks = chunks_by_sermon.get(sermon_id, [])
        if external_chunks and not entry.get("chunks"):
            entry = {**entry, "chunks": external_chunks}
        merged.append(entry)

    for sermon_id, external_chunks in chunks_by_sermon.items():
        if sermon_id in seen_sermon_ids or not external_chunks:
            continue
        first = external_chunks[0]
        merged.append({
            "sermon_id": sermon_id,
            "title": first.get("title", ""),
            "date": first.get("date", ""),
            "youtube_url": first.get("youtube_url", ""),
            "duration": first.get("duration", ""),
            "duration_seconds": safe_int(first.get("duration_seconds")),
            "pastor_name": first.get("pastor_name", ""),
            "summary": first.get("summary", ""),
            "topics": first.get("topics", []),
            "key_themes": first.get("key_themes", []),
            "scripture_references": first.get("scripture_references", []),
            "related_questions": first.get("related_questions", []),
            "transcript": " ".join(chunk.get("text", "") for chunk in external_chunks[:2])[:2000],
            "embedding": first.get("embedding"),
            "chunks": external_chunks,
        })

    print(f"Merged external chunk index: {sum(len(v) for v in chunks_by_sermon.values())} chunks")
    return merged


def load_optional_json(key):
    try:
        raw = s3.get_object(Bucket=BUCKET, Key=key)
        return json.loads(raw["Body"].read())
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"NoSuchKey", "404", "NotFound"}:
            return None
        raise


def get_retrieval_config():
    global _retrieval_config, _retrieval_config_loaded_at
    if not CONFIG_TABLE:
        return DEFAULT_RETRIEVAL_CONFIG

    now = datetime.now(timezone.utc)
    if _retrieval_config is not None and _retrieval_config_loaded_at:
        age = (now - _retrieval_config_loaded_at).total_seconds()
        if age < INDEX_TTL_SEC:
            return _retrieval_config

    try:
        table = dynamodb.Table(CONFIG_TABLE)
        item = table.get_item(Key={"configKey": "retrieval"}).get("Item") or {}
        _retrieval_config = {**DEFAULT_RETRIEVAL_CONFIG, **item}
        _retrieval_config_loaded_at = now
        return _retrieval_config
    except Exception as e:
        print(f"Retrieval config read error: {e}")
        return DEFAULT_RETRIEVAL_CONFIG


def filter_hidden_sermons(index, retrieval_config):
    hidden = set((retrieval_config or {}).get("hiddenSermons") or [])
    if not hidden:
        return index
    return [
        entry for entry in index
        if entry.get("sermon_id") not in hidden
        and entry.get("title") not in hidden
        and entry.get("youtube_url") not in hidden
    ]


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
    """Format top-K sermon excerpts for the Bedrock answer model's context window."""
    lines = ["Relevant sermon excerpts from the archive (retrieved by chunked hybrid search):\n"]

    for i, entry in enumerate(entries, 1):
        title      = entry.get("title", "Unknown")
        date       = entry.get("date", "Unknown date")
        pastor     = entry.get("pastor_name", "")
        topics     = ", ".join(entry.get("topics", []))
        scripture  = ", ".join(entry.get("scripture_references", []))
        description = entry.get("description", "")
        matched_chunks = entry.get("matched_chunks", [])
        transcript = entry.get("transcript", "")[:2000]

        lines.append(f"--- SERMON {i} ---")
        lines.append(f"Title: {title}")
        lines.append(f"Date: {date}")
        if pastor:    lines.append(f"Pastor: {pastor}")
        if topics:    lines.append(f"Topics: {topics}")
        if scripture: lines.append(f"Scripture: {scripture}")
        if description:
            lines.append(f"Description: {description}")

        if matched_chunks:
            lines.append("Matched excerpts:")
            for chunk in matched_chunks[:2]:
                lines.append(f"- {chunk.get('text', '')[:900]}")
        elif transcript:
            lines.append(f"Transcript:\n{transcript}")

        lines.append("")

    return "\n".join(lines)


def build_catalog_response():
    index = get_sermon_index()
    sermons = sorted(index, key=lambda e: e.get("date", ""), reverse=True)

    return {
        "sermon_count": len(sermons),
        "stats": build_archive_stats(sermons),
        "sermons": [
            {
                "sermon_id":            entry.get("sermon_id", ""),
                "title":                entry.get("title", ""),
                "date":                 entry.get("date", ""),
                "youtube_url":          entry.get("youtube_url", ""),
                "duration_seconds":     safe_int(entry.get("duration_seconds")),
                "pastor_name":          entry.get("pastor_name", ""),
                "description":          entry.get("description", ""),
                "topics":               entry.get("topics", []),
                "scripture_references": entry.get("scripture_references", [])
            }
            for entry in sermons
        ]
    }


def build_archive_stats(sermons):
    video_count = len(sermons)
    total_duration_seconds = sum(safe_int(entry.get("duration_seconds")) for entry in sermons)
    years = sorted({
        (entry.get("date") or "")[:4]
        for entry in sermons
        if (entry.get("date") or "")[:4].isdigit()
    })
    by_year = {}

    for entry in sermons:
        year = (entry.get("date") or "")[:4]
        if not year.isdigit():
            continue
        year_stats = by_year.setdefault(year, {"video_count": 0, "duration_seconds": 0})
        year_stats["video_count"] += 1
        year_stats["duration_seconds"] += safe_int(entry.get("duration_seconds"))

    return {
        "video_count": video_count,
        "sermon_count": video_count,
        "total_duration_seconds": total_duration_seconds,
        "total_duration_hours": round(total_duration_seconds / 3600, 1) if total_duration_seconds else 0,
        "year_start": years[0] if years else "",
        "year_end": years[-1] if years else "",
        "generated_at": _index_generated_at,
        "by_year": by_year,
    }


def safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# ── BEDROCK ────────────────────────────────────────────────────────────────

def system_prompt_for_language(preferred_language):
    if preferred_language == "ko":
        language_rule = "7. Respond entirely in Korean."
        format_rule = (
            "8. Do not include greetings, filler, or devotional sign-offs.\n"
            "9. Format the answer for easy scanning using markdown-like structure:\n"
            "   - Start each sermon entry with: #### 설교 N: \"설교 제목\" [YYYY-MM-DD]\n"
            "   - Under each sermon heading, use bullet points, not dashes in prose.\n"
            "   - Do not repeat citations such as [설교 1] or [Sermon 1] at the end of each bullet.\n"
            "   - Keep each bullet concise and tied to the question.\n"
            "10. If multiple sermons are relevant, list them in order of relevance. If helpful, end with one short synthesis paragraph."
        )
    else:
        language_rule = "7. Respond entirely in English."
        format_rule = (
            "8. Do not include greetings, filler, or devotional sign-offs.\n"
            "9. Format the answer for easy scanning using markdown-like structure:\n"
            "   - Start each sermon entry with: #### Sermon N: \"Sermon Title\" [YYYY-MM-DD]\n"
            "   - Under each sermon heading, use bullet points, not prose blocks where bullets fit.\n"
            "   - Do not repeat citations such as [Sermon 1] at the end of each bullet.\n"
            "   - Keep each bullet concise and tied to the question.\n"
            "10. If multiple sermons are relevant, list them in order of relevance. If helpful, end with one short synthesis paragraph."
        )
    return f"{SYSTEM_PROMPT}\n{language_rule}\n{format_rule}"


def invoke_bedrock(prompt, preferred_language="en"):
    resp = bedrock.converse(
        modelId=MODEL_ID,
        system=[{"text": system_prompt_for_language(preferred_language)}],
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

def normalize_language(value):
    return "ko" if str(value or "").strip().lower() == "ko" else "en"


def retrieval_config_version(retrieval_config=None):
    return str((retrieval_config or {}).get("version", "default"))


def question_hash(question, preferred_language="en", retrieval_config=None):
    normalized_language = normalize_language(preferred_language)
    key = f"{normalized_language}:{question.lower().strip()}"
    return hashlib.sha256(key.encode()).hexdigest()


def check_cache(question, preferred_language="en", retrieval_config=None):
    try:
        table = dynamodb.Table(CACHE_TABLE)
        item  = table.get_item(Key={"questionHash": question_hash(question, preferred_language, retrieval_config)}).get("Item")
        if item:
            if item.get("retrievalVersion") != RETRIEVAL_VERSION:
                print("Cache miss: retrieval version changed")
                return None
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


def cache_answer(question, result, preferred_language="en", retrieval_config=None):
    try:
        table = dynamodb.Table(CACHE_TABLE)
        now   = datetime.now(timezone.utc)
        table.put_item(Item={
            "questionHash":     question_hash(question, preferred_language, retrieval_config),
            "question":         question,
            "preferredLanguage": normalize_language(preferred_language),
            "answer":           result["answer"],
            "sermons_searched": result.get("sermons_searched", 0),
            "sources":          result.get("sources", []),
            "retrievalVersion": RETRIEVAL_VERSION,
            "configVersion":    retrieval_config_version(retrieval_config),
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


def log_retrieval_eval(user_id, question, answer, sermons, retrieval_config):
    if not EVAL_TABLE:
        return

    try:
        table = dynamodb.Table(EVAL_TABLE)
        now = datetime.now(timezone.utc)
        top_matches = []

        for sermon in sermons[:TOP_K]:
            top_matches.append({
                "sermon_id": sermon.get("sermon_id", ""),
                "title": sermon.get("title", ""),
                "date": sermon.get("date", ""),
                "match_score": str(round(float(sermon.get("match_score", 0) or 0), 4)),
                "matched_chunk_count": len(sermon.get("matched_chunks", [])),
            })

        table.put_item(Item={
            "evalId": str(uuid.uuid4()),
            "timestamp": now.isoformat(),
            "userId": user_id,
            "question": question,
            "answerPreview": answer[:500],
            "retrievalVersion": RETRIEVAL_VERSION,
            "configVersion": str((retrieval_config or {}).get("version", "default")),
            "topMatches": top_matches,
            "expiresAt": int(now.timestamp()) + (90 * 86400),
        })
    except Exception as e:
        print(f"Retrieval eval log error: {e}")


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
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS"
        },
        "body": json.dumps(body, ensure_ascii=False)
    }
