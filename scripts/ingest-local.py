#!/usr/bin/env python3
"""
Pulpit — Local Ingestion Script

Run this from your Mac after Sunday service to ingest new sermons.
Bypasses the AWS IP block that affects Lambda-based ingestion.

Usage:
    python3 scripts/ingest-local.py

Requirements:
    pip3 install youtube-transcript-api requests boto3 --break-system-packages

AWS credentials must be configured (aws configure).
"""

import json
import sys
import boto3
import requests
from datetime import datetime, timezone
from youtube_transcript_api import YouTubeTranscriptApi

# ── CONFIG ────────────────────────────────────────────────────────────────
BUCKET     = "pulpit-transcripts-dev-636305658578"
CHANNEL_ID = "UCchY0Iagf_2cCP0RGVwQ-FA"
API_KEY    = "AIzaSyBex44K219fpo81JC998_ObsRIaNo4E6Yg"
YEAR_FILTER = 2026  # only ingest sermons from this year onwards
MAX_RESULTS = 20

# ── INIT ──────────────────────────────────────────────────────────────────
s3  = boto3.client("s3", region_name="us-east-1")
api = YouTubeTranscriptApi()

print(f"Pulpit Local Ingest — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print(f"Channel: {CHANNEL_ID}")
print(f"Bucket:  {BUCKET}")
print("─" * 60)


def get_videos():
    resp = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "part":       "snippet",
            "channelId":  CHANNEL_ID,
            "order":      "date",
            "type":       "video",
            "eventType":  "completed",
            "maxResults": MAX_RESULTS,
            "key":        API_KEY
        },
        timeout=10
    )
    resp.raise_for_status()
    return [
        {
            "id":           item["id"]["videoId"],
            "title":        item["snippet"]["title"],
            "description":  item["snippet"]["description"],
            "published_at": item["snippet"]["publishedAt"],
        }
        for item in resp.json().get("items", [])
        if item.get("id", {}).get("videoId")
    ]


def transcript_exists(video_id):
    try:
        s3.head_object(
            Bucket=BUCKET,
            Key=f"transcripts/{video_id}.json"
        )
        return True
    except Exception:
        return False


def fetch_transcript(video_id):
    try:
        tlist      = api.list(video_id)
        transcript = next(iter(tlist))
        segments   = transcript.fetch()
        return " ".join([s.text for s in segments])
    except Exception as e:
        return None, str(e)


def store_sermon(video, transcript_text):
    sermon = {
        "sermon_id":   video["id"],
        "title":       video["title"],
        "date":        video["published_at"][:10],
        "youtube_url": f"https://youtube.com/watch?v={video['id']}",
        "description": video.get("description", "")[:500],
        "transcript":  transcript_text,
        "ingested_at": datetime.now(timezone.utc).isoformat()
    }
    key = f"transcripts/{sermon['date'][:4]}/{sermon['sermon_id']}.json"
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(sermon, ensure_ascii=False),
        ContentType="application/json"
    )
    return key


# ── MAIN ──────────────────────────────────────────────────────────────────
videos   = get_videos()
ingested = []
skipped  = []
errors   = []

for video in videos:
    vid   = video["id"]
    title = video["title"][:60]
    date  = video["published_at"][:10]

    if int(date[:4]) < YEAR_FILTER:
        print(f"  SKIP  {date}  {title[:50]} (pre-{YEAR_FILTER})")
        skipped.append(vid)
        continue

    if transcript_exists(vid):
        print(f"  EXIST {date}  {title[:50]}")
        skipped.append(vid)
        continue

    result = fetch_transcript(vid)
    if result is None or (isinstance(result, tuple) and result[0] is None):
        err = result[1] if isinstance(result, tuple) else "no transcript"
        print(f"  ERROR {date}  {title[:50]}")
        print(f"         → {err[:80]}")
        errors.append(vid)
        continue

    transcript_text = result if isinstance(result, str) else result[0]
    key = store_sermon(video, transcript_text)
    print(f"  ✅    {date}  {title[:50]}")
    ingested.append(vid)

print("─" * 60)
print(f"Ingested: {len(ingested)}  |  Skipped: {len(skipped)}  |  Errors: {len(errors)}")

if ingested:
    print(f"\nUploaded to s3://{BUCKET}/transcripts/")
