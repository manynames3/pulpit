"""
Pulpit — Admin Ingest Trigger Lambda

Queues ingestion work from a Cognito-protected admin API. The browser never
receives AWS credentials, YouTube API keys, direct Lambda permissions, or SQS
permissions.
"""

import json
import os
import boto3

sqs = boto3.client("sqs")

INGEST_QUEUE_URL = os.environ["INGEST_QUEUE_URL"]
ADMIN_GROUPS = {
    group.strip()
    for group in os.environ.get("ADMIN_GROUPS", "staff").split(",")
    if group.strip()
}


def lambda_handler(event, context):
    claims = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
    groups = parse_groups(claims.get("cognito:groups", ""))

    if ADMIN_GROUPS.isdisjoint(groups):
        return response(403, {"error": "Admin access is required."})

    body = parse_body(event)
    payload = {
        "trigger": "admin",
        "requested_by": claims.get("email") or claims.get("sub", "unknown"),
        "year_filter": int_or_default(body.get("yearFilter"), None),
        "max_new_per_run": int_or_default(body.get("maxNewPerRun"), 30),
        "max_transcript_attempts": int_or_default(body.get("maxTranscriptAttempts"), 30),
        "consecutive_exist_stop": int_or_default(body.get("consecutiveExistStop"), 120),
        "sleep_sec": float_or_default(body.get("sleepSec"), 8),
        "backfill": bool(body.get("backfill", False)),
    }

    # Drop unset values so the ingest Lambda can keep its own defaults.
    payload = {key: value for key, value in payload.items() if value is not None}

    queued = sqs.send_message(
        QueueUrl=INGEST_QUEUE_URL,
        MessageBody=json.dumps(payload, ensure_ascii=False),
        MessageAttributes={
            "trigger": {"DataType": "String", "StringValue": "admin"},
            "requested_by": {"DataType": "String", "StringValue": payload.get("requested_by", "unknown")},
        },
    )

    return response(202, {
        "status": "queued",
        "ingestQueue": INGEST_QUEUE_URL.split("/")[-1],
        "queueMessageId": queued.get("MessageId"),
        "payload": payload,
    })


def parse_groups(raw_groups):
    if isinstance(raw_groups, list):
        return set(raw_groups)
    if not raw_groups:
        return set()
    return {group.strip() for group in str(raw_groups).split(",") if group.strip()}


def parse_body(event):
    try:
        return json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return {}


def int_or_default(value, default):
    if value in (None, ""):
        return default
    return int(value)


def float_or_default(value, default):
    if value in (None, ""):
        return default
    return float(value)


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "POST,OPTIONS",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }
