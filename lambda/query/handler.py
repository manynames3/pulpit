"""
Pulpit — Query Lambda
Handles sermon search requests from authenticated users.
Applies Bedrock Guardrails, retrieves from Knowledge Base,
synthesizes a cited response, and logs the interaction.

Two user tiers via Cognito groups:
  member — sermon access only
  staff  — full access including board minutes and pastoral policies
"""

import json
import os
import uuid
import boto3
from datetime import datetime, timezone

bedrock   = boto3.client("bedrock-agent-runtime")
dynamodb  = boto3.resource("dynamodb")

MODEL_ID         = os.environ["BEDROCK_MODEL_ID"]
KB_ID            = os.environ["KNOWLEDGE_BASE_ID"]
GUARDRAIL_ID     = os.environ["GUARDRAIL_ID"]
GUARDRAIL_VER    = os.environ["GUARDRAIL_VERSION"]
TABLE_NAME       = os.environ["DYNAMODB_TABLE"]
PASTOR_CONTACT   = os.environ["PASTOR_CONTACT"]
ENVIRONMENT      = os.environ["ENVIRONMENT"]

CRISIS_KEYWORDS = [
    "suicide", "kill myself", "self harm", "abuse", "hurt myself",
    "don't want to live", "end my life", "hurting me"
]

SYSTEM_PROMPT = """You are Pulpit, a sermon research assistant for Atlanta Bethel Church.

Your purpose is to help members and staff find what has been taught in sermons.

Rules you must always follow:
1. Only answer based on the sermon content retrieved. Never generate theological positions not present in the sermons.
2. Always cite your source: sermon title, date, and scripture reference when available.
3. If a topic has not been addressed in the sermon archive, say so clearly and suggest speaking with a pastor.
4. Never give personal spiritual advice beyond what was taught from the pulpit.
5. Respond with warmth and respect — you are serving a faith community.

Response format:
- Direct answer grounded in the sermon content
- Citation: [Sermon Title — Date — Scripture Reference]
- If multiple sermons are relevant, list each with its citation
"""


def lambda_handler(event, context):
    """Entry point for API Gateway requests."""
    try:
        body      = json.loads(event.get("body", "{}"))
        question  = body.get("question", "").strip()
        claims    = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
        user_id   = claims.get("sub", "anonymous")
        user_groups = claims.get("cognito:groups", "member")

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

        # Retrieve and generate via Bedrock KB
        kb_response = bedrock.retrieve_and_generate(
            input={"text": question},
            retrieveAndGenerateConfiguration={
                "type": "KNOWLEDGE_BASE",
                "knowledgeBaseConfiguration": {
                    "knowledgeBaseId": KB_ID,
                    "modelArn": f"arn:aws:bedrock:us-east-1::foundation-model/{MODEL_ID}",
                    "generationConfiguration": {
                        "promptTemplate": {
                            "textPromptTemplate": SYSTEM_PROMPT + "\n\nQuestion: $query$\n\nRelevant sermon content:\n$search_results$"
                        },
                        "guardrailConfiguration": {
                            "guardrailId": GUARDRAIL_ID,
                            "guardrailVersion": GUARDRAIL_VER
                        }
                    }
                }
            }
        )

        answer   = kb_response["output"]["text"]
        citations = extract_citations(kb_response)

        # Log to DynamoDB for audit trail
        log_query(user_id, user_groups, question, answer, citations)

        return response(200, {
            "answer": answer,
            "cited_sermons": citations
        })

    except Exception as e:
        print(f"Error: {e}")
        return response(500, {"error": "Something went wrong. Please try again."})


def is_crisis_disclosure(text):
    """Detect crisis language before passing to Bedrock."""
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in CRISIS_KEYWORDS)


def extract_citations(kb_response):
    """Pull sermon citations from Bedrock retrieval metadata."""
    citations = []
    for citation in kb_response.get("citations", []):
        for ref in citation.get("retrievedReferences", []):
            metadata = ref.get("metadata", {})
            citations.append({
                "title":     metadata.get("title", "Unknown Sermon"),
                "date":      metadata.get("date", ""),
                "scripture": metadata.get("scripture_references", []),
                "url":       metadata.get("youtube_url", "")
            })
    return citations


def log_query(user_id, user_groups, question, answer, citations):
    """Write query to DynamoDB audit log."""
    table = dynamodb.Table(TABLE_NAME)
    now   = datetime.now(timezone.utc)

    # TTL: 90 days dev, 365 days prod
    ttl_days = 90 if ENVIRONMENT == "dev" else 365
    expires  = int(now.timestamp()) + (ttl_days * 86400)

    table.put_item(Item={
        "queryId":    str(uuid.uuid4()),
        "timestamp":  now.isoformat(),
        "userId":     user_id,
        "userGroup":  user_groups,
        "question":   question,
        "answer":     answer,
        "citations":  json.dumps(citations),
        "expiresAt":  str(expires)
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
