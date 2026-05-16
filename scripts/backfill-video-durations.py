#!/usr/bin/env python3
"""
Backfill YouTube duration metadata onto existing sermon transcript objects.

This is intentionally separate from transcript ingest: it only calls YouTube's
videos metadata endpoint, updates S3 JSON objects, then rebuilds the search
index so the frontend can show archive-level video/hour statistics.
"""

import json
import os
import re
from datetime import datetime, timezone

import boto3
import requests
from botocore.exceptions import ClientError


BUCKET = os.environ["PULPIT_TRANSCRIPT_BUCKET"]
API_KEY = os.environ["PULPIT_YOUTUBE_API_KEY"]
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
PREFIX = os.environ.get("PULPIT_TRANSCRIPT_PREFIX", "transcripts/")
PATCH_INDEX = os.environ.get("PULPIT_PATCH_INDEX", "1") == "1"
DRY_RUN = os.environ.get("PULPIT_DRY_RUN", "0") == "1"

s3 = boto3.client("s3", region_name=AWS_REGION)


def parse_youtube_duration(duration):
    match = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration or "")
    if not match:
        return 0
    days, hours, minutes, seconds = [int(value or 0) for value in match.groups()]
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def list_sermon_keys():
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=PREFIX):
        for item in page.get("Contents", []):
            key = item["Key"]
            if not key.endswith(".json"):
                continue
            if key.endswith("/index.json") or key == "transcripts/index.json":
                continue
            if "/skips/" in key:
                continue
            keys.append(key)
    return sorted(keys)


def load_json(key):
    raw = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    return json.loads(raw)


def load_optional_json(key):
    try:
        return load_json(key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"NoSuchKey", "404", "NotFound"}:
            return None
        raise


def put_json(key, body):
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )


def get_video_durations(video_ids):
    durations = {}
    unique_ids = [video_id for video_id in dict.fromkeys(video_ids) if video_id]

    for start in range(0, len(unique_ids), 50):
        batch = unique_ids[start:start + 50]
        response = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "contentDetails",
                "id": ",".join(batch),
                "maxResults": 50,
                "key": API_KEY,
            },
            timeout=10,
        )
        response.raise_for_status()
        for item in response.json().get("items", []):
            duration = item.get("contentDetails", {}).get("duration", "")
            durations[item["id"]] = {
                "duration": duration,
                "duration_seconds": parse_youtube_duration(duration),
            }

    return durations


def duration_changed(target, duration):
    duration_seconds = safe_int(duration.get("duration_seconds"))
    return target.get("duration") != duration.get("duration") or safe_int(target.get("duration_seconds")) != duration_seconds


def apply_duration(target, duration):
    target["duration"] = duration.get("duration", "")
    target["duration_seconds"] = safe_int(duration.get("duration_seconds"))


def patch_sermon_artifacts(sermon_id, duration):
    updated = 0
    card_key = f"sermons/{sermon_id}/card.json"
    card = load_optional_json(card_key)
    if isinstance(card, dict) and duration_changed(card, duration):
        apply_duration(card, duration)
        card["duration_backfilled_at"] = datetime.now(timezone.utc).isoformat()
        if not DRY_RUN:
            put_json(card_key, card)
        updated += 1

    chunks_key = f"sermons/{sermon_id}/chunks.json"
    payload = load_optional_json(chunks_key)
    chunks = payload.get("chunks", []) if isinstance(payload, dict) else []
    changed = False
    for chunk in chunks:
        if duration_changed(chunk, duration):
            apply_duration(chunk, duration)
            changed = True

    if changed:
        payload["duration_backfilled_at"] = datetime.now(timezone.utc).isoformat()
        if not DRY_RUN:
            put_json(chunks_key, payload)
        updated += 1

    return updated


def patch_index_durations(durations):
    updated = 0

    index = load_optional_json("transcripts/index.json")
    sermons = index.get("sermons", []) if isinstance(index, dict) else []
    for sermon in sermons:
        duration = durations.get(sermon.get("sermon_id"))
        if not duration:
            continue
        if duration_changed(sermon, duration):
            apply_duration(sermon, duration)
            updated += 1
        for chunk in sermon.get("chunks", []):
            if duration_changed(chunk, duration):
                apply_duration(chunk, duration)

    if updated and not DRY_RUN:
        index["duration_backfilled_at"] = datetime.now(timezone.utc).isoformat()
        put_json("transcripts/index.json", index)

    chunk_index = load_optional_json("indexes/chunk-index.json")
    chunks = chunk_index.get("chunks", []) if isinstance(chunk_index, dict) else []
    chunk_updates = 0
    for chunk in chunks:
        duration = durations.get(chunk.get("sermon_id"))
        if duration and duration_changed(chunk, duration):
            apply_duration(chunk, duration)
            chunk_updates += 1

    if chunk_updates and not DRY_RUN:
        chunk_index["duration_backfilled_at"] = datetime.now(timezone.utc).isoformat()
        put_json("indexes/chunk-index.json", chunk_index)

    return {
        "sermon_index_entries_updated": updated,
        "chunk_index_entries_updated": chunk_updates,
    }


def main():
    keys = list_sermon_keys()
    sermons = []

    for key in keys:
        sermon = load_json(key)
        sermon_id = sermon.get("sermon_id") or key.rsplit("/", 1)[-1].replace(".json", "")
        sermons.append((key, sermon_id, sermon))

    durations = get_video_durations([sermon_id for _, sermon_id, _ in sermons])
    updated = 0
    artifact_updates = 0
    missing = []
    total_seconds = 0

    for key, sermon_id, sermon in sermons:
        duration = durations.get(sermon_id)
        if not duration:
            missing.append(sermon_id)
            continue

        duration_seconds = safe_int(duration.get("duration_seconds"))
        total_seconds += duration_seconds
        if duration_changed(sermon, duration):
            apply_duration(sermon, duration)
            sermon["duration_backfilled_at"] = datetime.now(timezone.utc).isoformat()
            if not DRY_RUN:
                put_json(key, sermon)
            updated += 1

        artifact_updates += patch_sermon_artifacts(sermon_id, duration)

    index_updates = patch_index_durations(durations) if PATCH_INDEX else {}

    print(json.dumps({
        "dry_run": DRY_RUN,
        "sermons_seen": len(sermons),
        "raw_sermons_updated": updated,
        "artifact_files_updated": artifact_updates,
        "videos_with_duration": len(durations),
        "missing_youtube_metadata": len(missing),
        "total_duration_hours": round(total_seconds / 3600, 1),
        **index_updates,
    }, indent=2))

    if missing:
        print(f"Missing YouTube metadata for: {', '.join(missing)}")


if __name__ == "__main__":
    main()
