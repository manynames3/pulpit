"""
Lambda entrypoint for the Pulpit query API.

The retrieval, Bedrock, cache, audit, and catalog logic lives in query_service.py
so this file stays focused on the AWS Lambda handler contract.
"""

import json
import traceback

from query_service import answer_question, build_catalog_response, response


def lambda_handler(event, context):
    try:
        http_method = event.get("httpMethod", "")
        resource = event.get("resource", "")

        if http_method == "OPTIONS":
            return response(204, {})

        if http_method == "GET" and resource == "/catalog":
            return response(200, build_catalog_response())

        body = json.loads(event.get("body") or "{}")
        question = body.get("question", "").strip()
        if not question:
            return response(400, {"error": "Question is required."})

        claims = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
        user_id = claims.get("sub", "anonymous")
        user_groups = claims.get("cognito:groups", "member")

        return response(200, answer_question(question, user_id, user_groups))

    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()
        return response(500, {"error": "Something went wrong. Please try again."})
