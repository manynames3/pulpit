"""
Pulpit — Query Lambda (v1 pilot)

Reads sermon transcripts directly from S3 and passes them to Claude.
No vector database required for 2026-only pilot (~16 sermons).

How it works:
1. List all transcript JSONs from S3
2. Load each one (small JSON files, fast)
3. Simple relevance filter: keep sermons containing query keywords
4. Pass filtered transcripts + question to Claude via Bedrock
5. Claude returns a cited answer grounded in actual sermon content

Upgrade path: when archive grows beyond ~50 sermons, add a vector store
(Pinecone free tier, OpenSearch Serverless, or RDS pgvector) and switch
to Bedrock KB RetrieveAndGenerate instead of direct S3 fetch.
"""

import json
import os
import uuid
import boto3
from datetime import datetime, timezone

s3       = boto3.client("s3")
bedrock  = boto3.client("bedrock-runtime")
dynamodb = boto3.resource("dynamodb")

MODEL_ID         = os.environ["BEDROCK_MODEL_ID"]
BUCKET           = os.environ["TRANSCRIPT_BUCKET"]
GUARDRAIL_ID     = os.environ["GUARDRAIL_ID"]
GUARDRAIL_VER    = os.environ["GUARDRAIL_VERSION"]
TABLE_NAME       = os.environ["DYNAMODB_TABLE"]
PASTOR_CONTACT   = os.environ["PASTOR_CONTACT"]
ENVIRONMENT      = os.environ["ENVIRONMENT"]

CRISIS_KEYWORDS = [
    "suicide", "kill myself", "self harm", "abuse", "hurt myself",
    "don't want to live", "end my life", "hurting me"
]

SYSTEM_PROMPT = """You are Pulpit, a sermon research assistant for {church}.

Your only purpose is to help members and staff find what has been taught in sermons.

Rules you must always follow:
1. Only answer based on the sermon transcripts provided. Never generate theological positions not present in the sermons.
2. Always cite your source: sermon title and date for every claim.
3. If the topic has not been addressed in the provided sermons, say so clearly.
4. Never give personal spiritual advice beyond what was taught from the pulpit.
5. Respond with warmth — you are serving a faith community.

Format:
- Direct answer grounded in the sermon content
- Citation: [Sermon Title — Date]
- If multiple sermons are relevant, list each with its citation"""


def lambda_handler(event, context):
    try:
        body         = json.loads(event.get("body", "{}"))
        question     = body.get("question", "").strip()
        claims       = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
        user_id      = claims.get("sub", "anonymous")
        user_groups  = claims.get("cognito:groups", "member")

        if not question:
            return response(400, {"error": "Question is required."})

        # Crisis detection — redirect before hitting Bedrock
        if is_crisis_disclosure(question):
            return response(200, {
                "answer": f"It sounds like you may be going through something difficult. "
                          f"Please reach out to our pastoral team directly — they are here for you. "
                          f"{PASTOR_CONTACT}",
                "cited_sermons": [],
                "crisis_redirect": True
            })

        # Load transcripts from S3
        sermons = load_sermons(question)

        if not sermons:
            return response(200, {
                "answer": "I don't have any sermons in the archive yet. "
                          "Check back after the next ingestion run.",
                "cited_sermons": []
            })

        # Build prompt with sermon context
        sermon_context = build_sermon_context(sermons)
        prompt = f"{sermon_context}\n\nQuestion: {question}"

        # Call Bedrock with guardrails
        answer = invoke_bedrock(prompt)

        # Log to DynamoDB
        log_query(user_id, user_groups, question, answer)

        return response(200, {
            "answer": answer,
            "sermons_searched": len(sermons)
        })

    except Exception as e:
        print(f"Error: {e}")
        return response(500, {"error": "Something went wrong. Please try again."})


def load_sermons(question):
    """
    Load sermon transcripts from S3.
    For v1 pilot (~16 sermons) this is fast — each file is small JSON.
    Filters to sermons containing at least one query keyword for relevance.
    """
    sermons = []
    keywords = [w.lower() for w in question.split() if len(w) > 3]

    try:
        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=BUCKET, Prefix="transcripts/")

        for page in pages:
            for obj in page.get("Contents", []):
                if not obj["Key"].endswith(".json"):
                    continue

                raw = s3.get_object(Bucket=BUCKET, Key=obj["Key"])
                sermon = json.loads(raw["Body"].read())

                # Basic relevance filter — keep if any keyword found
                transcript_lower = sermon.get("transcript", "").lower()
                title_lower = sermon.get("title", "").lower()
                combined = transcript_lower + " " + title_lower

                if not keywords or any(kw in combined for kw in keywords):
                    sermons.append(sermon)

        # Sort by date descending — most recent first
        sermons.sort(key=lambda s: s.get("date", ""), reverse=True)

        # Cap at 5 most relevant to keep prompt size manageable
        return sermons[:5]

    except Exception as e:
        print(f"Error loading sermons: {e}")
        return []


def build_sermon_context(sermons):
    """Format sermon transcripts for Claude's context window."""
    lines = ["Here are the relevant sermon transcripts:\n"]

    for i, sermon in enumerate(sermons, 1):
        title    = sermon.get("title", "Unknown")
        date     = sermon.get("date", "Unknown date")
        scriptures = ", ".join(sermon.get("scripture_references", []))
        transcript = sermon.get("transcript", "")[:3000]  # cap per sermon

        lines.append(f"--- SERMON {i} ---")
        lines.append(f"Title: {title}")
        lines.append(f"Date: {date}")
        if scriptures:
            lines.append(f"Scripture: {scriptures}")
        lines.append(f"Transcript excerpt:\n{transcript}\n")

    return "\n".join(lines)


def invoke_bedrock(prompt):
    """Call Bedrock with guardrails applied."""
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}]
    })

    resp = bedrock.invoke_model(
        modelId=MODEL_ID,
        body=body,
        guardrailIdentifier=GUARDRAIL_ID,
        guardrailVersion=GUARDRAIL_VER
    )

    result = json.loads(resp["body"].read())
    return result["content"][0]["text"]


def is_crisis_disclosure(text):
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in CRISIS_KEYWORDS)


def log_query(user_id, user_groups, question, answer):
    table = dynamodb.Table(TABLE_NAME)
    now   = datetime.now(timezone.utc)
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


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(body)
    }
