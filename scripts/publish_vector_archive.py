#!/usr/bin/env python3
"""Migrate an existing index without re-embedding; publish a complete snapshot last."""

import argparse
import json
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambda" / "query"))
from archive_store import MANIFEST_KEY, publish_snapshot


def publish(bucket, region, index_arn, source=None, manifest_key=MANIFEST_KEY):
    s3 = boto3.client("s3", region_name=region)
    if source:
        payload = json.loads(Path(source).read_text())
    else:
        payload = json.loads(s3.get_object(Bucket=bucket, Key="transcripts/index.json")["Body"].read())
    config = Path(__file__).resolve().parents[1] / "lambda/query/retrieval_synonyms.json"
    aliases = json.loads(config.read_text()).get("english_aliases", {})
    result = publish_snapshot(s3, boto3.client("s3vectors", region_name=region), bucket, payload,
                              index_arn, aliases, manifest_key)
    print(json.dumps({k: result[k] for k in ("snapshot", "chunk_count", "generated_at", "index_arn")}))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--index-arn", required=True)
    parser.add_argument("--source", help="Local legacy index JSON; otherwise read transcripts/index.json")
    parser.add_argument("--manifest-key", default=MANIFEST_KEY)
    args = parser.parse_args()
    publish(args.bucket, args.region, args.index_arn, args.source, args.manifest_key)
