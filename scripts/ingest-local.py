#!/usr/bin/env python3
"""
Pulpit — Local Ingestion Script

Run from your Mac after Sunday service to ingest new sermons.
Bypasses the AWS IP block that affects Lambda-based ingestion.

Uses the YouTube uploads playlist API (not search API).
The search API only returns ~4 results for this channel.
The uploads playlist returns all 3,000+ videos with pagination.

Usage:
    python3 scripts/ingest-local.py

Requirements:
    pip3 install youtube-transcript-api requests boto3 --break-system-packages
"""

import json
import time
import boto3
import requests
from datetime import datetime, timezone
from youtube_transcript_api import YouTubeTranscriptApi

# ── CONFIG ────────────────────────────────────────────────────────────────
BUCKET      = "pulpit-transcripts-dev-636305658578"
CHANNEL_ID  = "UCchY0Iagf_2cCP0RGVwQ-FA"
API_KEY     = "AIzaSyBex44K219fpo81JC998_ObsRIaNo4E6Yg"
YEAR_FILTER = 2026

# Uploads playlist = channel ID with UC replaced by UU
UPLOADS_PLAYLIST = CHANNEL_ID.replace("UC", "UU", 1)

# ── INIT ──────────────────────────────────────────────────────────────────
s3  = boto3.client("s3", region_name="us-east-1")
api = YouTubeTranscriptApi()

print(f"Pulpit Local Ingest — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print(f"Channel:  {CHANNEL_ID}")
print(f"Playlist: {UPLOADS_PLAYLIST}")
print(f"Bucket:   {BUCKET}")
print("─" * 60)


def get_videos():
    """
    Fetch all 2026+ videos using the uploads playlist API.

    Why not search API: YouTube search returns ~4 results for this
    channel regardless of maxResults. Known API limitation.

    Uploads playlist returns all videos reliably with pagination.
    Stops as soon as a pre-YEAR_FILTER video is encountered.
    """
    videos     = []
    page_token = None
    page_num   = 1

    while True:
        params = {
            "part":       "snippet",
            "playlistId": UPLOADS_PLAYLIST,
            "maxResults": 50,
            "key":        API_KEY
        }
        if page_token:
            params["pageToken"] = page_token

        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/playlistItems",
            params=params,
            timeout=10
        )
        resp.raise_for_status()
        data  = resp.json()
        items = data.get("items", [])

        hit_old = False
        for item in items:
            snippet = item["snippet"]
            vid_id  = snippet["resourceId"]["videoId"]
            date    = snippet["publishedAt"][:10]

            if int(date[:4]) < YEAR_FILTER:
                hit_old = True
                break

            videos.append({
                "id":           vid_id,
                "title":        snippet["title"],
                "description":  snippet.get("description", ""),
                "published_at": snippet["publishedAt"],
            })

        next_page = data.get("nextPageToken")

        if hit_old or not next_page:
            break

        print(f"  Page {page_num}: {len(videos)} videos so far...")
        page_token = next_page
        page_num  += 1

    return videos


def transcript_exists(video_id):
    try:
        s3.head_object(Bucket=BUCKET, Key=f"transcripts/{video_id}.json")
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
print(f"Found {len(videos)} videos from {YEAR_FILTER}+\n")

ingested = []
skipped  = []
errors   = []

for video in videos:
    vid   = video["id"]
    title = video["title"][:55]
    date  = video["published_at"][:10]

    if transcript_exists(vid):
        print(f"  EXIST {date}  {title}")
        skipped.append(vid)
        continue

    # Skip videos that are clearly not sermons
    # Shorts, announcements, highlights rarely have transcripts
    skip_keywords = ["#shorts", "교회소식", "하이라이트", "간증 영상",
                     "소풍", "수련회", "달란트", "Lock-In", "lock-in",
                     "환영인사", "감사의 말씀ㅣ", "소개"]
    if any(kw.lower() in title.lower() for kw in skip_keywords):
        print(f"  SKIP  {date}  {title} (non-sermon)")
        skipped.append(vid)
        continue

    result = fetch_transcript(vid)

    if isinstance(result, tuple):
        err = result[1]
        print(f"  ERROR {date}  {title}")
        print(f"         → {err[:80]}")
        errors.append(vid)
        time.sleep(2)  # back off on error
        continue

    store_sermon(video, result)
    print(f"  ✅    {date}  {title}")
    ingested.append(vid)
    time.sleep(1)  # be polite to YouTube

print("─" * 60)
print(f"Ingested: {len(ingested)}  |  Already exists: {len(skipped)}  |  Errors: {len(errors)}")

if ingested:
    print(f"\nUploaded to s3://{BUCKET}/transcripts/")
