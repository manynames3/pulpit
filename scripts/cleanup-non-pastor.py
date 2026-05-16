#!/usr/bin/env python3
"""
Remove sermons not by 이혜진 from the archive.

Moves non-matching JSONs to transcripts/YEAR/skips/ so they won't be
re-ingested, then rebuilds index.json.

Usage:
    python3 scripts/cleanup-non-pastor.py
"""

import json
import os
import boto3
from datetime import datetime, timezone

BUCKET     = os.environ["PULPIT_TRANSCRIPT_BUCKET"]
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
TARGET_PASTOR = "이혜진"

s3 = boto3.client("s3", region_name=AWS_REGION)

print(f"Pulpit Cleanup — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print(f"Bucket:  {BUCKET}")
print(f"Keeping: {TARGET_PASTOR} only")
print("─" * 60)

# ── List all sermon JSONs ──────────────────────────────────────────────────

paginator = s3.get_paginator("list_objects_v2")
pages = paginator.paginate(Bucket=BUCKET, Prefix="transcripts/")

all_keys = []
for page in pages:
    for obj in page.get("Contents", []):
        key = obj["Key"]
        # Skip index.json and anything already in skips/
        if key.endswith("index.json") or "/skips/" in key:
            continue
        if key.endswith(".json"):
            all_keys.append(key)

print(f"Found {len(all_keys)} sermon files\n")

kept    = []
removed = []

for key in sorted(all_keys):
    raw  = s3.get_object(Bucket=BUCKET, Key=key)
    data = json.loads(raw["Body"].read())

    pastor = data.get("pastor_name", "")
    title  = data.get("title", key)[:70]

    if TARGET_PASTOR in pastor:
        kept.append(key)
        print(f"  ✅ Keep  — {pastor:<20} {title}")
    else:
        # Move to skips/ so ingest won't re-process
        parts    = key.split("/")           # ['transcripts', 'YEAR', 'ID.json']
        skip_key = "/".join(parts[:-1]) + "/skips/" + parts[-1]

        # Copy then delete (S3 has no native move)
        s3.copy_object(
            Bucket=BUCKET,
            CopySource={"Bucket": BUCKET, "Key": key},
            Key=skip_key
        )
        s3.delete_object(Bucket=BUCKET, Key=key)

        removed.append(key)
        display_pastor = pastor if pastor else "(no pastor extracted)"
        print(f"  🗑  Remove — {display_pastor:<20} {title}")

print(f"\n─ Summary ─────────────────────────────────────────────────────")
print(f"  Kept:    {len(kept)}")
print(f"  Removed: {len(removed)}")

# ── Rebuild index.json ────────────────────────────────────────────────────

print(f"\nRebuilding index.json with {len(kept)} sermons...")

sermons = []
for key in kept:
    raw  = s3.get_object(Bucket=BUCKET, Key=key)
    data = json.loads(raw["Body"].read())
    sermons.append({
        "id":                  data.get("video_id", ""),
        "title":               data.get("title", ""),
        "date":                data.get("date", ""),
        "pastor_name":         data.get("pastor_name", ""),
        "topics":              data.get("topics", []),
        "key_themes":          data.get("key_themes", []),
        "scripture_references": data.get("scripture_references", []),
        "transcript":          data.get("transcript", "")[:2000],
        "embedding":           data.get("embedding"),
    })

sermons.sort(key=lambda s: s["date"], reverse=True)

index = {
    "generated_at":  datetime.now(timezone.utc).isoformat(),
    "sermon_count":  len(sermons),
    "embedding_count": sum(1 for s in sermons if s.get("embedding")),
    "sermons":       sermons,
}

s3.put_object(
    Bucket=BUCKET,
    Key="transcripts/index.json",
    Body=json.dumps(index, ensure_ascii=False),
    ContentType="application/json",
)

print(f"  ✅ index.json → s3://{BUCKET}/transcripts/index.json")
print(f"     {index['sermon_count']} sermons, {index['embedding_count']} with embeddings")
