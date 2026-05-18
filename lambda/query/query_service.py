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
import unicodedata
import boto3
from collections import Counter
from datetime import datetime, timezone

from botocore.exceptions import ClientError

s3       = boto3.client("s3")
bedrock  = boto3.client("bedrock-runtime")
dynamodb = boto3.resource("dynamodb")

PLANNER_MODEL_ID  = os.environ["BEDROCK_MODEL_PLANNER"]
RERANKER_MODEL_ID = os.environ["BEDROCK_MODEL_RERANKER"]
ANSWER_MODEL_ID   = os.environ["BEDROCK_MODEL_ANSWER"]
EMBED_MODEL_ID    = "amazon.titan-embed-text-v2:0"
BUCKET            = os.environ["TRANSCRIPT_BUCKET"]
GUARDRAIL_ID      = os.environ["GUARDRAIL_ID"]
GUARDRAIL_VER     = os.environ["GUARDRAIL_VERSION"]
LOG_TABLE         = os.environ["DYNAMODB_TABLE"]
CACHE_TABLE       = os.environ["CACHE_TABLE"]
CONFIG_TABLE      = os.environ.get("CONFIG_TABLE")
EVAL_TABLE        = os.environ.get("EVAL_TABLE")
PASTOR_CONTACT    = os.environ["PASTOR_CONTACT"]
ENVIRONMENT       = os.environ["ENVIRONMENT"]
LEAD_PASTOR       = os.environ.get("LEAD_PASTOR_NAME", "이혜진 목사")

TOP_K                    = 5     # sermons sent to the Bedrock answer model
CHUNK_CANDIDATE_LIMIT    = 50    # candidate chunk hits before collapsing to sermons
PER_SUBQUERY_CHUNK_LIMIT = 80    # wide raw pool; diversified before answer context
CHUNK_DIVERSITY_PER_SERMON = 3
MATCHED_CHUNKS_PER_SERMON = 3
ANSWER_CONTEXT_CHAR_LIMIT = 15000
RERANK_SNIPPET_CHAR_LIMIT = 220
FALLBACK_LIMIT           = 30    # max sermons if index has no embeddings yet
CACHE_TTL_DAYS           = 30
INDEX_TTL_SEC            = 600   # reload index every 10 min to pick up new sermons
MIN_RELEVANCE_SCORE = 0.35
EXPANDED_RELEVANCE_SCORE = 0.30
MIN_HYBRID_SCORE = 0.28
MIN_CHUNK_SEMANTIC_SCORE = 0.22
RETRIEVAL_VERSION = "v15-planned-union-retrieval"
TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
ASCII_TERM_RE = re.compile(r"^[a-z0-9]+$")
HANGUL_RE = re.compile(r"[가-힣]")
_HANGUL = re.compile(r"[\uAC00-\uD7A3\u1100-\u11FF\u3130-\u318F]")
_KO_TOKEN_RE = re.compile(r"[0-9A-Za-z\uAC00-\uD7A3\u1100-\u11FF\u3130-\u318F]+")
_KO_TOKEN_TRIM = ".,!?;:'\"()[]{}「」『』【】—–…·"
QUERY_BUNDLE_LIMIT = 8
QUERY_EXPANSION_TERM_LIMIT = 12

METADATA_TOPIC_STOP_TERMS = {
    "주일예배",
    "주일 예배",
    "주일 설교",
    "금요설교",
    "금요예배",
    "새벽예배",
    "새벽 이슬 예배",
    "새벽 만나 예배",
    "온라인 예배",
    "신년 예배",
    "신년특별새벽예배",
    "새성전 입당 특별새벽예배",
    "고난주간",
    "신앙",
    "예배",
}

ENGLISH_LEXICAL_ALIASES = {
    "fail": "fail",
    "fails": "fail",
    "failed": "fail",
    "failing": "fail",
    "failure": "fail",
    "failures": "fail",
}

ENGLISH_STOP_TERMS = {
    "a", "about", "all", "an", "and", "any", "archive", "are", "as", "ask",
    "be", "by", "can", "christian", "could", "did", "do", "does", "find", "for", "from",
    "had", "has", "have", "he", "her", "him", "his", "i", "in", "is", "it",
    "latest", "me", "most", "my", "of", "on", "or", "pastor", "please",
    "preach", "preached", "preaching", "question", "recent", "recently",
    "search", "sermon", "sermons", "she", "show", "tell", "that", "the",
    "there", "this", "to", "teach", "teaching", "taught", "was", "were", "what", "when", "where", "who",
    "why", "with", "you",
}

DEFAULT_RETRIEVAL_CONFIG = {
    "configKey": "retrieval",
    "version": "default",
    "preferredSermons": [],
    "hiddenSermons": [],
}

STATIC_QUERY_VARIANTS = {
    "wilderness": ["광야"],
    "광야": ["wilderness", "민수기", "출애굽", "이스라엘 광야", "광야 생활"],
    "history": ["역사", "성경 역사", "구속사"],
    "역사": ["history", "성경 역사", "이스라엘 역사", "구속사"],
    "archaeology": ["고고학", "발굴", "유물", "유적"],
    "archeology": ["고고학", "발굴", "유물", "유적"],
    "고고학": ["archaeology", "발굴", "유물", "유적"],
    "noah": ["노아"],
    "ark": ["방주", "노아", "홍수"],
    "noahs ark": ["노아", "방주", "홍수"],
    "noah's ark": ["노아", "방주", "홍수"],
    "flood": ["홍수", "노아"],
    "flooding": ["홍수", "노아"],
    "pillar of cloud": ["구름기둥"],
    "cloud pillar": ["구름기둥"],
    "cloud column": ["구름기둥"],
    "column of cloud": ["구름기둥"],
    "pillar of fire": ["불기둥"],
    "fire column": ["불기둥"],
    "peter": ["베드로"],
    "apostle peter": ["베드로"],
    "money": ["돈"],
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

RECENT_QUERY_TERMS = ("최근", "최근에", "요즘", "latest", "recent", "recently")

KOREAN_LEXICAL_ALIASES = {
    "자만": ["교만", "오만"],
    "교만": ["자만", "오만"],
    "오만": ["자만", "교만"],
}

KOREAN_SINGLE_CHAR_TERMS = {
    "돈", "죄", "시", "창", "출", "민", "신", "왕", "마", "막", "눅", "요", "행", "롬", "계",
}

_CROSSWALK_GROUPS = [
    {"noah", "노아"},
    {"noah's ark", "noahs ark", "ark", "노아의 방주", "방주"},
    {"the flood", "flood", "대홍수", "홍수"},
    {"wilderness", "광야"},
    {"history", "역사"},
    {"archaeology", "archeology", "고고학"},
    {"peter", "apostle peter", "베드로"},
    {"money", "돈", "재물", "물질"},
    {"death", "죽음", "사망"},
    {"pillar of cloud", "cloud pillar", "cloud column", "column of cloud", "구름기둥"},
    {"pillar of fire", "fire pillar", "fire column", "불기둥"},
    {"moses", "모세"},
    {"abraham", "아브라함"},
    {"david", "다윗"},
    {"jesus", "예수", "예수님"},
    {"christ", "그리스도"},
    {"holy spirit", "성령", "성령님"},
    {"prayer", "기도"},
    {"faith", "믿음"},
    {"grace", "은혜"},
    {"salvation", "구원"},
    {"gospel", "복음"},
    {"resurrection", "부활"},
    {"cross", "십자가"},
    {"sin", "죄"},
    {"forgiveness", "용서"},
    {"love", "사랑"},
    {"church", "교회"},
    {"sermon", "설교"},
    {"genesis", "창세기", "창"},
    {"exodus", "출애굽기", "출"},
    {"psalms", "psalm", "시편", "시"},
    {"proverbs", "잠언", "잠"},
    {"isaiah", "이사야", "사"},
    {"matthew", "마태복음", "마태", "마"},
    {"mark", "마가복음", "막"},
    {"luke", "누가복음", "눅"},
    {"john", "요한복음", "요"},
    {"acts", "사도행전", "행"},
    {"romans", "로마서", "롬"},
    {"revelation", "요한계시록", "계"},
]

_CROSSWALK_INDEX = {}
for _group in _CROSSWALK_GROUPS:
    for _term in _group:
        _CROSSWALK_INDEX[_term.lower()] = _group

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
_query_variant_cache = {}


def answer_question(question, user_id="anonymous", user_groups="member"):
    """Run the sermon archive query workflow and return a JSON-serializable result."""
    question = (question or "").strip()
    answer_language = answer_language_for_question(question)

    # Crisis detection — redirect before hitting Bedrock.
    if is_crisis_disclosure(question):
        return {
            "answer": crisis_redirect_answer(answer_language),
            "crisis_redirect": True,
            "answer_language": answer_language,
        }

    retrieval_config = get_retrieval_config()

    # 1. Cache check — identical questions cost nothing.
    cached = check_cache(question, answer_language, retrieval_config)
    if cached:
        return {**cached, "cached": True, "answer_language": answer_language}

    # 2. Analyze the natural-language request before retrieval.
    question_analysis = analyze_question(question, bedrock)

    # 3. Semantic search across full archive.
    sermons = find_relevant_sermons(question, retrieval_config, question_analysis)
    if not sermons:
        return {
            "answer": no_results_answer(answer_language),
            "sources": [],
            "answer_language": answer_language,
        }

    # 4. Generate answer.
    sermon_context = build_sermon_context(sermons)
    prompt         = f"{sermon_context}\n\nQuestion: {question}"
    answer         = invoke_bedrock(prompt, answer_language)

    # 5. Cache + audit log.
    sources = [
        {
            "title":       e.get("title", ""),
            "date":        e.get("date", ""),
            "youtube_url": e.get("youtube_url", ""),
        }
        for e in sermons
    ]
    result = {
        "answer": answer,
        "sermons_searched": len(sermons),
        "sources": sources,
        "answer_language": answer_language,
    }
    cache_answer(question, result, answer_language, retrieval_config)
    log_query(
        user_id,
        user_groups,
        question,
        answer,
        question_type=question_analysis.get("type"),
        subquery_count=len(question_analysis.get("subqueries") or []),
    )
    log_retrieval_eval(user_id, question, answer, sermons, retrieval_config)

    return result


# ── QUESTION PLANNING ──────────────────────────────────────────────────────

def analyze_question(question: str, bedrock_client) -> dict:
    prompt = (
        "You analyze a user's question for a bilingual Korean/English church sermon archive.\n"
        "Return ONLY one JSON object. Do not include preamble, explanation, markdown, or code fences.\n\n"
        "Return this exact structure:\n"
        "{\n"
        "  \"type\": \"simple\" | \"detailed\" | \"comparison\",\n"
        "  \"subqueries\": [\"<english query>\", \"<korean query>\", \"<scripture term if detected>\"],\n"
        "  \"scripture_refs\": [\"<book chapter:verse if detected>\"],\n"
        "  \"language\": \"en\" | \"ko\" | \"mixed\"\n"
        "}\n\n"
        "Rules:\n"
        "- If the question mentions a Bible reference such as Romans 8, John 3:16, 요한복음 3장, or 창세기 6장, "
        "extract it into scripture_refs and also include it as a subquery.\n"
        "- Always generate at least one Korean subquery and one English subquery.\n"
        "- Keep subqueries concise and useful for retrieval from sermon titles, topics, scripture references, descriptions, and transcript chunks.\n"
        "- Use standard Korean Bible/church vocabulary when translating English concepts.\n"
        "- Return ONLY the JSON object. No preamble. No markdown fences.\n\n"
        f"Question: {question}"
    )

    fallback = {
        "type": "detailed",
        "subqueries": [question],
        "scripture_refs": [],
        "language": "en",
    }

    try:
        resp = bedrock_client.converse(
            modelId=PLANNER_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 500, "temperature": 0.0}
        )
        raw = resp["output"]["message"]["content"][0]["text"]
        parsed = extract_json_object(raw)
        if not isinstance(parsed, dict):
            return fallback

        question_type = parsed.get("type")
        if question_type not in {"simple", "detailed", "comparison"}:
            question_type = "detailed"

        language = parsed.get("language")
        if language not in {"en", "ko", "mixed"}:
            language = detect_question_language(question)

        subqueries = clean_string_list(parsed.get("subqueries"))
        scripture_refs = clean_string_list(parsed.get("scripture_refs"))
        for ref in scripture_refs:
            if ref not in subqueries:
                subqueries.append(ref)
        if not subqueries:
            subqueries = [question]

        analysis = {
            "type": question_type,
            "subqueries": dedupe_strings(subqueries),
            "scripture_refs": dedupe_strings(scripture_refs),
            "language": language,
        }
        print(
            f"Question analysis: type={analysis['type']}, "
            f"language={analysis['language']}, subqueries={analysis['subqueries']}"
        )
        return analysis
    except Exception as e:
        print(f"Question analysis error: {e}")
        return fallback


def clean_string_list(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def dedupe_strings(values):
    result = []
    seen = set()
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def detect_question_language(question):
    has_korean = bool(HANGUL_RE.search(question or ""))
    has_english = any("a" <= ch.lower() <= "z" for ch in question or "")
    if has_korean and has_english:
        return "mixed"
    if has_korean:
        return "ko"
    return "en"


# ── SEMANTIC SEARCH ────────────────────────────────────────────────────────

def find_relevant_sermons(question, retrieval_config=None, question_analysis=None):
    """Rank archive results and return top sermons with matched excerpts."""
    retrieval_config = retrieval_config or DEFAULT_RETRIEVAL_CONFIG
    index = filter_hidden_sermons(get_sermon_index(), retrieval_config)
    if not index:
        return []

    if any(entry.get("chunks") for entry in index):
        chunk_results = find_relevant_sermons_from_chunks(index, question, retrieval_config, question_analysis)
        if chunk_results:
            return chunk_results

    entries_with_embeddings = [e for e in index if e.get("embedding")]
    return find_relevant_sermons_by_sermon_embedding(index, entries_with_embeddings, question, retrieval_config)


def find_relevant_sermons_by_sermon_embedding(index, entries_with_embeddings, question, retrieval_config=None):
    if entries_with_embeddings:
        query_bundle = build_query_bundle(index, question)
        ranked = rank_sermons(entries_with_embeddings, query_bundle, retrieval_config)
        if ranked:
            keyword_ranked = rerank_keyword_matches(ranked, " ".join(query_bundle))
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


def find_relevant_sermons_from_chunks(index, question, retrieval_config=None, question_analysis=None):
    subqueries = subqueries_for_retrieval(question, question_analysis)
    chunk_hits = retrieve_union(subqueries, index, retrieval_config=retrieval_config, top_n=CHUNK_CANDIDATE_LIMIT)
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

    expanded_hits = expand_neighbors(chunk_hits, flatten_index_chunks(index), window=1)
    reranked_hits = rerank_evidence_chunks(question, question_analysis or {}, expanded_hits)
    return collapse_chunk_hits_to_sermons(reranked_hits, retrieval_config)


def subqueries_for_retrieval(question, question_analysis=None):
    analysis_subqueries = []
    if isinstance(question_analysis, dict):
        analysis_subqueries = clean_string_list(question_analysis.get("subqueries"))

    subqueries = [question] + analysis_subqueries
    return dedupe_strings([query for query in subqueries if query.strip()])


def retrieve_union(subqueries: list[str], index: list, retrieval_config=None, top_n: int = CHUNK_CANDIDATE_LIMIT) -> list:
    union = {}
    per_subquery_limit = max(PER_SUBQUERY_CHUNK_LIMIT, top_n * 2)

    for subquery in subqueries:
        query_variants = build_query_bundle(index, subquery, include_llm_expansion=False)
        for hit in rank_chunk_hits(
            index,
            query_variants,
            subquery,
            retrieval_config=retrieval_config,
            limit=per_subquery_limit,
        ):
            chunk_id = hit.get("chunk_id")
            if not chunk_id:
                continue

            existing = union.get(chunk_id)
            if not existing or hit.get("score", 0) > existing.get("score", 0):
                union[chunk_id] = hit

    hits = sorted(
        union.values(),
        key=lambda item: (
            item.get("score", item.get("combined_score", 0)),
            item.get("lexical_score", 0),
            item.get("semantic_score", 0),
        ),
        reverse=True,
    )
    diversified = diversify_chunk_hits(hits, top_n=top_n)
    print(
        f"Union retrieval: {len(subqueries)} subqueries produced {len(hits)} unique chunks "
        f"across {count_unique_sermons(hits)} sermons; diversified to {len(diversified)} chunks "
        f"across {count_unique_sermons(diversified)} sermons"
    )
    return diversified


def diversify_chunk_hits(hits, top_n=CHUNK_CANDIDATE_LIMIT, per_sermon_limit=CHUNK_DIVERSITY_PER_SERMON):
    if not hits or top_n <= 0:
        return []
    if per_sermon_limit <= 0:
        return hits[:top_n]

    selected = []
    selected_ids = set()
    sermon_counts = Counter()

    for hit in hits:
        chunk_id = hit.get("chunk_id")
        if not chunk_id or chunk_id in selected_ids:
            continue

        sermon_id = hit.get("sermon_id") or hit.get("entry", {}).get("sermon_id") or ""
        if sermon_counts[sermon_id] >= per_sermon_limit:
            continue

        selected.append(hit)
        selected_ids.add(chunk_id)
        sermon_counts[sermon_id] += 1
        if len(selected) >= top_n:
            return selected

    # If a narrow query only has a few sermons, fill the remaining slots rather
    # than discarding valid evidence.
    for hit in hits:
        chunk_id = hit.get("chunk_id")
        if not chunk_id or chunk_id in selected_ids:
            continue
        selected.append(hit)
        selected_ids.add(chunk_id)
        if len(selected) >= top_n:
            break

    return selected


def count_unique_sermons(hits):
    return len({
        hit.get("sermon_id") or hit.get("entry", {}).get("sermon_id")
        for hit in hits
        if hit.get("sermon_id") or hit.get("entry", {}).get("sermon_id")
    })


def flatten_index_chunks(index):
    flattened = []
    for entry in index:
        chunks = sorted(
            entry.get("chunks", []),
            key=lambda chunk: safe_int(chunk.get("chunk_index")),
        )
        for chunk in chunks:
            flattened.append(build_chunk_hit(entry, chunk, 0.0, 0, 0.0))
    return flattened


def expand_neighbors(chunks: list, all_chunks: list, window: int = 1) -> list:
    if not chunks or not all_chunks:
        return chunks

    positions = {
        chunk.get("chunk_id"): idx
        for idx, chunk in enumerate(all_chunks)
        if chunk.get("chunk_id")
    }
    expanded = {
        chunk.get("chunk_id"): chunk
        for chunk in chunks
        if chunk.get("chunk_id")
    }

    for chunk in list(chunks):
        chunk_id = chunk.get("chunk_id")
        position = positions.get(chunk_id)
        if position is None:
            continue

        sermon_id = chunk.get("sermon_id")
        base_score = chunk.get("score", chunk.get("combined_score", 0))
        for offset in range(-window, window + 1):
            if offset == 0:
                continue
            neighbor_position = position + offset
            if neighbor_position < 0 or neighbor_position >= len(all_chunks):
                continue

            neighbor = all_chunks[neighbor_position]
            neighbor_id = neighbor.get("chunk_id")
            if not neighbor_id or neighbor_id in expanded:
                continue
            if neighbor.get("sermon_id") != sermon_id:
                continue

            neighbor_score = max(neighbor.get("score", 0), base_score * 0.92)
            expanded[neighbor_id] = {
                **neighbor,
                "semantic_score": max(neighbor.get("semantic_score", 0), chunk.get("semantic_score", 0) * 0.92),
                "lexical_score": neighbor.get("lexical_score", 0),
                "combined_score": neighbor_score,
                "score": neighbor_score,
                "neighbor": True,
            }

    results = sorted(
        expanded.values(),
        key=lambda item: (
            item.get("score", item.get("combined_score", 0)),
            item.get("lexical_score", 0),
            item.get("semantic_score", 0),
        ),
        reverse=True,
    )
    print(f"Neighbor expansion: {len(chunks)} chunks expanded to {len(results)} chunks")
    return results


def rerank_evidence_chunks(question, question_analysis, chunk_hits):
    if not chunk_hits:
        return []

    candidates = sorted(
        chunk_hits,
        key=lambda item: item.get("score", item.get("combined_score", 0)),
        reverse=True,
    )[:CHUNK_CANDIDATE_LIMIT]

    payload_lines = []
    for hit in candidates:
        entry = hit.get("entry", {})
        chunk = hit.get("chunk", {})
        text = re.sub(r"\s+", " ", chunk.get("text", "")).strip()[:RERANK_SNIPPET_CHAR_LIMIT]
        payload_lines.append(
            json.dumps({
                "chunk_id": hit.get("chunk_id", ""),
                "title": entry.get("title", ""),
                "date": entry.get("date", ""),
                "scripture_refs": entry.get("scripture_references", []),
                "score": round(float(hit.get("score", hit.get("combined_score", 0)) or 0), 4),
                "text": text,
            }, ensure_ascii=False)
        )

    prompt = (
        "You rerank sermon evidence chunks for a bilingual church sermon archive.\n"
        "Return ONLY a JSON object with this exact shape: {\"chunk_ids\":[\"chunk id\"]}\n"
        "Order chunk_ids from most useful to least useful for answering the user question.\n"
        "Prefer chunks that directly address the question over generic similarity.\n"
        "Do not add IDs that are not present.\n\n"
        f"Question type: {question_analysis.get('type', 'detailed') if isinstance(question_analysis, dict) else 'detailed'}\n"
        f"Question: {question}\n\n"
        "Candidate chunks:\n"
        + "\n".join(payload_lines)
    )

    try:
        resp = bedrock.converse(
            modelId=RERANKER_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 900, "temperature": 0.0}
        )
        raw = resp["output"]["message"]["content"][0]["text"]
        parsed = extract_json_object(raw)
        ordered_ids = clean_string_list((parsed or {}).get("chunk_ids") if isinstance(parsed, dict) else [])
        if not ordered_ids:
            return candidates

        by_id = {hit.get("chunk_id"): hit for hit in candidates if hit.get("chunk_id")}
        ordered = [by_id[chunk_id] for chunk_id in ordered_ids if chunk_id in by_id]
        remaining = [hit for hit in candidates if hit.get("chunk_id") not in set(ordered_ids)]
        print(f"Reranked {len(ordered)} chunks with {len(remaining)} score-ordered fallbacks")
        return ordered + remaining
    except Exception as e:
        print(f"Rerank error: {e}")
        return candidates


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


def build_query_bundle(index, question, include_llm_expansion=True):
    variants = []
    seen = {question.strip().lower()}

    expanded_query_terms = sorted(
        expand_query(question),
        key=lambda item: (item != question.strip().lower(), len(item), item),
    )
    candidates = expanded_query_terms + static_query_variants(question) + extract_literal_terms(question)
    if include_llm_expansion:
        candidates.extend(expand_query_variants(question))

    for candidate in candidates:
        core = normalize_variant(candidate)
        if not core:
            continue
        key = core.lower()
        if key in seen:
            continue
        seen.add(key)
        variants.append(core)

    exact_matches = []
    semantic_fallbacks = []
    for variant in variants:
        if archive_contains_term(index, variant):
            exact_matches.append(variant)
        elif should_keep_semantic_variant(variant):
            semantic_fallbacks.append(variant)

    expanded = exact_matches[:QUERY_BUNDLE_LIMIT - 1]
    remaining = QUERY_BUNDLE_LIMIT - 1 - len(expanded)
    if remaining > 0:
        expanded.extend(semantic_fallbacks[:remaining])

    if expanded:
        print(f"Query bundle for '{question}': {expanded}")
    return [question] + expanded


def static_query_variants(question):
    normalized = re.sub(r"\s+", " ", question.strip().lower())
    normalized = normalized.replace("’", "'")
    matches = []
    for key, values in STATIC_QUERY_VARIANTS.items():
        if normalized == key or key in normalized:
            matches.extend(values)
    return matches


def should_keep_semantic_variant(variant):
    tokens = extract_literal_terms(variant)
    return bool(tokens) and len(variant) <= 80


def rank_chunk_hits(index, queries, question, retrieval_config=None, limit=CHUNK_CANDIDATE_LIMIT):
    vectors = []
    for query in queries:
        vec = embed_text(query)
        if vec:
            vectors.append(vec)

    primary_terms = extract_literal_terms(question)
    terms = collect_search_terms([question] + queries)
    if not vectors and not terms:
        return []
    hits = []

    for entry in index:
        for chunk in entry.get("chunks", []):
            lexical_score = lexical_match_score(entry, terms, chunk.get("text", ""))
            primary_lexical_score = lexical_match_score(entry, primary_terms, chunk.get("text", ""))
            embedding = chunk.get("embedding")
            semantic_score = max(cosine_similarity(vec, embedding) for vec in vectors) if embedding and vectors else 0.0
            combined_score = (
                semantic_score
                + lexical_bonus(lexical_score)
                + primary_lexical_bonus(primary_lexical_score)
            )

            if semantic_score <= 0 and lexical_score <= 0:
                continue

            hits.append(build_chunk_hit(
                entry,
                chunk,
                semantic_score,
                lexical_score,
                combined_score,
                primary_lexical_score,
            ))

    hits.sort(
        key=lambda item: (
            configured_priority(item["entry"], retrieval_config),
            item["combined_score"],
            item["lexical_score"],
            item["semantic_score"],
            pastor_priority(item["entry"]),
            item["entry"].get("date", ""),
        ),
        reverse=True,
    )
    return hits[:limit]


def build_chunk_hit(entry, chunk, semantic_score, lexical_score, combined_score, primary_lexical_score=0):
    chunk_id = chunk_identifier(entry, chunk)
    sermon_id = entry.get("sermon_id") or chunk.get("sermon_id") or ""
    return {
        "entry": entry,
        "chunk": chunk,
        "chunk_id": chunk_id,
        "sermon_id": sermon_id,
        "semantic_score": semantic_score,
        "lexical_score": lexical_score,
        "primary_lexical_score": primary_lexical_score,
        "combined_score": combined_score,
        "score": combined_score,
    }


def chunk_identifier(entry, chunk):
    explicit_id = chunk.get("chunk_id") or chunk.get("id")
    if explicit_id:
        return str(explicit_id)

    sermon_id = entry.get("sermon_id") or chunk.get("sermon_id") or entry.get("youtube_url") or entry.get("title", "")
    chunk_index = chunk.get("chunk_index")
    if chunk_index is not None:
        return f"{sermon_id}:{chunk_index}"

    text_hash = hashlib.sha256((chunk.get("text", "")[:500]).encode()).hexdigest()[:16]
    return f"{sermon_id}:{text_hash}"


def collapse_chunk_hits_to_sermons(chunk_hits, retrieval_config=None):
    sermons = {}

    for hit in chunk_hits:
        entry = hit["entry"]
        sermon_id = entry.get("sermon_id")
        if sermon_id not in sermons:
            sermons[sermon_id] = {
                **entry,
                "match_score": hit.get("score", hit["combined_score"]),
                "matched_chunks": [],
            }

        sermon = sermons[sermon_id]
        score = hit.get("score", hit["combined_score"])
        sermon["match_score"] = max(sermon["match_score"], score)
        sermon["matched_chunks"].append({
            "chunk_id": hit.get("chunk_id", ""),
            "text": hit["chunk"].get("text", ""),
            "semantic_score": hit["semantic_score"],
            "lexical_score": hit["lexical_score"],
            "combined_score": hit["combined_score"],
            "score": score,
            "neighbor": hit.get("neighbor", False),
        })

    results = []
    for sermon in sermons.values():
        sermon["matched_chunks"] = sorted(
            sermon["matched_chunks"],
            key=lambda chunk: (chunk["score"], chunk["lexical_score"], chunk["semantic_score"]),
            reverse=True,
        )[:MATCHED_CHUNKS_PER_SERMON]
        results.append(sermon)

    results.sort(
        key=lambda item: (
            configured_priority(item, retrieval_config),
            item.get("match_score", 0),
            pastor_priority(item),
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
            normalized = normalize_english_lexical_token(cleaned)
            candidates = [] if normalized in ENGLISH_STOP_TERMS else [normalized]

        for term in candidates:
            if term in seen:
                continue
            seen.add(term)
            terms.append(term)

    return terms


def normalize_korean_lexical_terms(token):
    terms = []
    seen = set()

    for candidate in _ko_tokens(token):
        if is_korean_stop_term(candidate):
            continue
        add_search_term(terms, seen, candidate)

        for alias_key, aliases in KOREAN_LEXICAL_ALIASES.items():
            if korean_lexical_score(alias_key, candidate) <= 0 and korean_lexical_score(candidate, alias_key) <= 0:
                continue
            add_search_term(terms, seen, alias_key)
            for alias in aliases:
                add_search_term(terms, seen, alias)

    return terms


def add_search_term(terms, seen, candidate):
    normalized = str(candidate or "").strip().lower()
    if not normalized or normalized in seen:
        return
    seen.add(normalized)
    terms.append(normalized)


def is_korean_stop_term(token):
    return any(korean_lexical_score(stop_term, token) > 0 for stop_term in KOREAN_STOP_TERMS)


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
    """Use exact token matches for English and token-aware matching for Korean."""
    normalized = (term or "").lower().strip()
    if not normalized:
        return 0
    if ASCII_TERM_RE.fullmatch(normalized):
        lexical_term = normalize_english_lexical_token(normalized)
        return Counter(tokenize(text)).get(lexical_term, 0)
    if HANGUL_RE.search(normalized):
        return korean_lexical_score(normalized, text)
    return (text or "").lower().count(normalized)


def _ko_normalize(text: str) -> str:
    """NFD-decompose Korean so related syllable forms share jamo prefixes."""
    return unicodedata.normalize("NFD", text or "")


def _ko_tokens(text: str) -> list[str]:
    """Return Korean-bearing tokens while dropping particles and other one-char noise."""
    tokens = []
    for raw in _KO_TOKEN_RE.findall(text or ""):
        token = raw.strip(_KO_TOKEN_TRIM).lower()
        if not token or not _HANGUL.search(token):
            continue
        if len(token) < 2 and token not in KOREAN_SINGLE_CHAR_TERMS:
            continue
        tokens.append(token)
    return tokens


def korean_lexical_score(query: str, document: str) -> float:
    """
    Token-aware Korean match score in [0.0, 1.0].
    NFD jamo normalization lets 고고학 match 고고학자들/고고학적 without suffix tables.
    """
    q_tokens = _ko_tokens(query)
    if not q_tokens:
        return 0.0

    d_tokens = _ko_tokens(document)
    if not d_tokens:
        return 0.0

    d_norm_tokens = [_ko_normalize(token) for token in d_tokens]
    matched = 0
    for token in q_tokens:
        q_norm = _ko_normalize(token)
        if any(q_norm in d_norm or d_norm in q_norm for d_norm in d_norm_tokens):
            matched += 1

    return matched / len(q_tokens)


def expand_query(query: str) -> set[str]:
    """
    Return search terms for a query, including English/Korean vocabulary equivalents.
    This does not normalize embedding input; it only adds additional query variants.
    """
    expanded = set()
    q = (query or "").lower().replace("’", "'").strip()
    if not q:
        return expanded

    q_clean = re.sub(r"'s?\b", "", q).strip()

    for candidate in {q, q_clean}:
        if candidate:
            expanded.add(candidate)
        if candidate in _CROSSWALK_INDEX:
            expanded.update(term.lower() for term in _CROSSWALK_INDEX[candidate])

    words = q_clean.split()
    for i in range(len(words) - 1):
        phrase = f"{words[i]} {words[i + 1]}"
        if phrase in _CROSSWALK_INDEX:
            expanded.update(term.lower() for term in _CROSSWALK_INDEX[phrase])

    for token in _KO_TOKEN_RE.findall(q_clean):
        token = token.strip(_KO_TOKEN_TRIM).lower()
        if token in _CROSSWALK_INDEX:
            expanded.update(term.lower() for term in _CROSSWALK_INDEX[token])

    return expanded


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


def primary_lexical_bonus(score):
    if score <= 0:
        return 0.0
    return min(score / 12.0, 0.35)


def minimum_term_length(term):
    if re.fullmatch(r"[가-힣]", term or "") and term in KOREAN_SINGLE_CHAR_TERMS:
        return 1
    return 2


def expand_query_variants(question):
    if not should_expand_query(question):
        return []

    cache_key = question.strip().lower()
    if cache_key in _query_variant_cache:
        return _query_variant_cache[cache_key]

    prompt = (
        "You are preparing a search query for a bilingual Korean/English church sermon archive. "
        "Convert the user's keyword or sentence into concise retrieval terms that would likely appear in sermon titles, "
        "topics, scripture references, descriptions, or transcript chunks.\n\n"
        "Rules:\n"
        "- Support both English and Korean input.\n"
        "- The archive is primarily Korean sermon content, so English input should usually produce Korean retrieval terms first.\n"
        "- Extract the user's core topic, Bible person, place, doctrine, event, emotion, or question concept.\n"
        "- Include Korean equivalents for English terms and English equivalents for Korean terms when useful.\n"
        "- Prefer standard Korean Bible/church vocabulary over casual paraphrases.\n"
        "- Remove filler words such as recent, pastor, sermon, question, did, does, about, please.\n"
        "- Do not invent a sermon title or answer the question.\n"
        "- Return strict JSON only, with this shape: "
        "{\"search_terms\":[\"term\"],\"semantic_queries\":[\"short query\"]}\n"
        "- search_terms: up to 12 short exact terms, ordered best first.\n"
        "- semantic_queries: up to 4 short bilingual semantic rewrites, ordered best first.\n\n"
        "Examples:\n"
        "Query: money\n"
        "{\"search_terms\":[\"돈\",\"재물\",\"물질\",\"헌금\",\"money\"],\"semantic_queries\":[\"돈과 물질에 대한 설교\",\"Christian teaching about money\"]}\n"
        "Query: 최근에 목사님이 자만에 대한 설교를 했나요\n"
        "{\"search_terms\":[\"자만\",\"교만\",\"오만\",\"pride\"],\"semantic_queries\":[\"자만과 교만에 대한 설교\",\"recent sermon about pride\"]}\n"
        "Query: cloud column\n"
        "{\"search_terms\":[\"구름기둥\",\"불기둥\",\"출애굽\",\"cloud pillar\"],\"semantic_queries\":[\"구름기둥에 대한 설교\",\"pillar of cloud in Exodus\"]}\n\n"
        f"Query: {question}"
    )

    try:
        resp = bedrock.converse(
            modelId=PLANNER_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 300, "temperature": 0.0}
        )
        raw = resp["output"]["message"]["content"][0]["text"]
        variants = parse_query_expansion_response(raw)
        _query_variant_cache[cache_key] = variants
        return variants
    except Exception as e:
        print(f"Query expansion error: {e}")
        _query_variant_cache[cache_key] = []
        return []


def should_expand_query(question):
    q = question.strip()
    if not q or len(q) > 180:
        return False
    return any("a" <= ch.lower() <= "z" for ch in q) or not is_literal_keyword_query(q)


def parse_query_expansion_response(raw):
    payload = extract_json_object(raw)
    candidates = []

    if payload:
        for key in ("search_terms", "semantic_queries"):
            values = payload.get(key, [])
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, list):
                continue
            candidates.extend(values)
    else:
        candidates = [
            re.sub(r"^\s*(?:[-*\d.)]+)\s*", "", line).strip()
            for line in str(raw or "").splitlines()
        ]

    variants = []
    seen = set()
    for candidate in candidates:
        for expanded_candidate in split_expansion_candidate(candidate):
            normalized = normalize_variant(expanded_candidate)
            if not normalized or len(normalized) > 80:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            variants.append(normalized)

    return variants[:QUERY_EXPANSION_TERM_LIMIT]


def extract_json_object(raw):
    text = str(raw or "").strip()
    if not text:
        return None

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def split_expansion_candidate(candidate):
    text = str(candidate or "").strip()
    if not text:
        return []

    parts = re.split(r"[,;/]|(?:\s+\|\s+)", text)
    return [part.strip() for part in parts if part.strip()]


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
        if HANGUL_RE.search(needle):
            if korean_lexical_score(needle, hay) > 0:
                return True
        elif needle in hay:
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
            sorted_chunks = sorted(
                matched_chunks,
                key=lambda chunk: chunk.get("score", chunk.get("combined_score", 0)),
                reverse=True,
            )
            for chunk in sorted_chunks[:MATCHED_CHUNKS_PER_SERMON]:
                lines.append(f"- {chunk.get('text', '')[:850]}")
        elif transcript:
            lines.append(f"Transcript:\n{transcript}")

        lines.append("")

    context = "\n".join(lines)
    if len(context) > ANSWER_CONTEXT_CHAR_LIMIT:
        return context[:ANSWER_CONTEXT_CHAR_LIMIT].rsplit("\n", 1)[0] + "\n\n[Context truncated to stay under the answer model limit.]"
    return context


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
                "description":          entry.get("description") or entry.get("summary", ""),
                "topics":               entry.get("topics", []),
                "key_themes":           entry.get("key_themes", []),
                "scripture_references": entry.get("scripture_references", [])
            }
            for entry in sermons
        ]
    }


def normalize_metadata_terms(value):
    if not value:
        return []

    if isinstance(value, list):
        raw_terms = value
    elif isinstance(value, str):
        raw_terms = re.split(r"[,;\n]+", value)
    else:
        return []

    terms = []
    for raw_term in raw_terms:
        if not isinstance(raw_term, str):
            continue

        term = re.sub(r"\s+", " ", raw_term).strip(" \t\r\n.,!?;:'\"()[]{}")
        if not term:
            continue

        if term.lower() in METADATA_TOPIC_STOP_TERMS:
            continue

        terms.append(term)

    return terms


def build_ranked_metadata_terms(sermons, field, limit=10):
    counts = Counter()
    labels = {}

    for entry in sermons:
        seen_for_sermon = set()
        for term in normalize_metadata_terms(entry.get(field)):
            key = term.lower()
            if key in seen_for_sermon:
                continue

            seen_for_sermon.add(key)
            labels.setdefault(key, term)
            counts[key] += 1

    return [
        {"label": labels[key], "count": count}
        for key, count in counts.most_common(limit)
    ]


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
        "top_topics": build_ranked_metadata_terms(sermons, "topics"),
        "top_lessons": build_ranked_metadata_terms(sermons, "key_themes"),
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
        modelId=ANSWER_MODEL_ID,
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


def answer_language_for_question(question, question_analysis=None):
    """Answer in the language the user searched with, not the selected UI language."""
    if HANGUL_RE.search(question or ""):
        return "ko"

    if isinstance(question_analysis, dict) and question_analysis.get("language") == "ko":
        return "ko"

    return "en"


def no_results_answer(answer_language):
    if answer_language == "ko":
        return (
            "해당 주제를 분명하게 다루는 설교를 아카이브에서 찾지 못했습니다. "
            "더 넓은 키워드, 성경 본문, 또는 더 구체적인 설교 질문으로 다시 검색해 보세요."
        )

    return (
        "I could not find a sermon in the archive that clearly addresses that topic. "
        "Try a broader keyword, a Bible passage, or a more specific sermon question."
    )


def crisis_redirect_answer(answer_language):
    if answer_language == "ko":
        return (
            "지금 많이 어려운 시간을 지나고 계실 수 있습니다. "
            "목회팀에 직접 연락해 주세요. 목회팀이 함께 도와드릴 수 있습니다. "
            f"{PASTOR_CONTACT}"
        )

    return (
        "It sounds like you may be going through something difficult. "
        "Please reach out to our pastoral team directly. They are here for you. "
        f"{PASTOR_CONTACT}"
    )


def retrieval_config_version(retrieval_config=None):
    return str((retrieval_config or {}).get("version", "default"))


def question_hash(question, preferred_language="en", retrieval_config=None):
    normalized_language = normalize_language(preferred_language)
    config_version = retrieval_config_version(retrieval_config)
    key = f"{normalized_language}:{config_version}:{question.lower().strip()}"
    return hashlib.sha256(key.encode()).hexdigest()


def check_cache(question, preferred_language="en", retrieval_config=None):
    try:
        table = dynamodb.Table(CACHE_TABLE)
        item  = table.get_item(Key={"questionHash": question_hash(question, preferred_language, retrieval_config)}).get("Item")
        if item:
            if item.get("configVersion") != retrieval_config_version(retrieval_config):
                print("Cache miss: retrieval config changed")
                return None
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

def log_query(user_id, user_groups, question, answer, question_type=None, subquery_count=1):
    try:
        table    = dynamodb.Table(LOG_TABLE)
        now      = datetime.now(timezone.utc)
        ttl_days = 90 if ENVIRONMENT == "dev" else 365
        table.put_item(Item={
            "queryId":        str(uuid.uuid4()),
            "timestamp":      now.isoformat(),
            "userId":         user_id,
            "userGroup":      user_groups,
            "question":       question,
            "answer":         answer,
            "question_type":  question_type or "detailed",
            "subquery_count": int(subquery_count or 0),
            "expiresAt":      str(int(now.timestamp()) + (ttl_days * 86400))
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
