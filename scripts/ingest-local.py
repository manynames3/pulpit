#!/usr/bin/env python3
"""
Pulpit — Local Ingestion Script

Run from your Mac after Sunday service to ingest new sermons.
Bypasses the AWS IP block that affects Lambda-based ingestion.

Uses the YouTube uploads playlist API (not search API).
The search API only returns ~4 results for this channel.
The uploads playlist returns all 3,000+ videos with pagination.

Usage:
    # set env vars (see below), then:
    python3 scripts/ingest-local.py

Requirements:
    pip3 install youtube-transcript-api requests boto3 --break-system-packages
"""

import json
import os
import time
import boto3
import requests
from datetime import datetime, timezone
from youtube_transcript_api import YouTubeTranscriptApi

# ── CONFIG ────────────────────────────────────────────────────────────────
#
# Provide config via environment variables so this can run safely as a cron job.
#
# Required:
#   - PULPIT_TRANSCRIPT_BUCKET
#   - PULPIT_YOUTUBE_CHANNEL_ID
#   - PULPIT_YOUTUBE_API_KEY
#
# Optional:
#   - PULPIT_YEAR_FILTER (default: current year)
#   - AWS_REGION (default: us-east-1)
#
BUCKET = os.environ["PULPIT_TRANSCRIPT_BUCKET"]
CHANNEL_ID = os.environ["PULPIT_YOUTUBE_CHANNEL_ID"]
API_KEY = os.environ["PULPIT_YOUTUBE_API_KEY"]
YEAR_FILTER = int(os.environ.get("PULPIT_YEAR_FILTER", str(datetime.now().year)))
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Throttle controls to avoid YouTube IP blocks.
# Default behavior: ingest only a small batch each run (cron will pick up the rest).
MAX_NEW_PER_RUN = int(os.environ.get("PULPIT_MAX_NEW_PER_RUN", "12"))
SLEEP_BETWEEN_TRANSCRIPTS_SEC = float(os.environ.get("PULPIT_SLEEP_SEC", "2.5"))
MAX_TRANSCRIPT_ATTEMPTS_PER_RUN = int(os.environ.get("PULPIT_MAX_TRANSCRIPT_ATTEMPTS", "40"))

# Uploads playlist = channel ID with UC replaced by UU
UPLOADS_PLAYLIST = CHANNEL_ID.replace("UC", "UU", 1)

# ── INIT ──────────────────────────────────────────────────────────────────
s3 = boto3.client("s3", region_name=AWS_REGION)
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
        # Stored as transcripts/<year>/<video_id>.json
        s3.head_object(Bucket=BUCKET, Key=f"transcripts/{YEAR_FILTER}/{video_id}.json")
        return True
    except Exception:
        # Also treat permanently skipped videos as "handled" so we don't retry forever.
        try:
            s3.head_object(Bucket=BUCKET, Key=f"transcripts/{YEAR_FILTER}/skips/{video_id}.json")
            return True
        except Exception:
            return False


def fetch_transcript(video_id):
    """
    Try to fetch captions via youtube-transcript-api.
    Some videos legitimately have no transcripts available (disabled/unprocessed/etc).
    """
    try:
        tlist = api.list(video_id)

        # Prefer Korean/English if available, otherwise take first available transcript.
        try:
            transcript = tlist.find_transcript(["ko", "en"])
        except Exception:
            transcript = next(iter(tlist))

        segments = transcript.fetch()
        return " ".join([s.text for s in segments]), None
    except Exception as e:
        # Normalize message for logs (some exceptions stringify poorly / include newlines)
        msg = " ".join(str(e).split())
        if not msg:
            msg = e.__class__.__name__
        return None, msg


def looks_like_ip_block(err_msg: str) -> bool:
    m = (err_msg or "").lower()
    return "blocking requests from your ip" in m or "ipblockedexception" in m or "requestblocked" in m


def looks_like_subtitles_disabled(err_msg: str) -> bool:
    return "subtitles are disabled" in (err_msg or "").lower()


def mark_permanent_skip(video, reason: str):
    """
    Persist a "do not retry" marker in S3 for videos that will never yield a transcript
    (e.g., subtitles disabled). This prevents repeated transcript API calls on every run.
    """
    key = f"transcripts/{YEAR_FILTER}/skips/{video['id']}.json"
    body = {
        "video_id": video["id"],
        "title": video.get("title", ""),
        "published_at": video.get("published_at", ""),
        "youtube_url": f"https://youtube.com/watch?v={video['id']}",
        "reason": reason,
        "marked_at": datetime.now(timezone.utc).isoformat(),
    }
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(body, ensure_ascii=False),
        ContentType="application/json"
    )


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
print(f"Max new ingests this run: {MAX_NEW_PER_RUN} (set PULPIT_MAX_NEW_PER_RUN to change)\n")

ingested = []
skipped  = []
errors   = []
transcript_attempts = 0

# How many consecutive already-ingested videos before we assume
# we've caught up and stop iterating. Avoids re-walking the full
# archive on every run (and burning transcript API quota).
CONSECUTIVE_EXIST_STOP = int(os.environ.get("PULPIT_CONSECUTIVE_EXIST_STOP", "5"))
consecutive_exists = 0

for video in videos:
    vid   = video["id"]
    title = video["title"][:55]
    date  = video["published_at"][:10]

    if transcript_exists(vid):
        print(f"  EXIST {date}  {title}")
        skipped.append(vid)
        consecutive_exists += 1
        if consecutive_exists >= CONSECUTIVE_EXIST_STOP:
            print(f"\n{CONSECUTIVE_EXIST_STOP} consecutive existing videos — archive is caught up. Stopping.")
            break
        continue

    consecutive_exists = 0  # reset on any non-existing video

    # Only ingest sermons by senior pastor 이혜진
    if "이혜진" not in video["title"]:
        print(f"  SKIP  {date}  {title} (not 이혜진 pastor)")
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

    if transcript_attempts >= MAX_TRANSCRIPT_ATTEMPTS_PER_RUN:
        print(f"\nReached PULPIT_MAX_TRANSCRIPT_ATTEMPTS={MAX_TRANSCRIPT_ATTEMPTS_PER_RUN}. Stopping to avoid IP blocks.")
        break

    transcript_text, err = fetch_transcript(vid)
    transcript_attempts += 1

    if not transcript_text:
        if looks_like_ip_block(err):
            print(f"  STOP  {date}  {title} (YouTube IP block detected)")
            print(f"         → {err}")
            print("         → Exiting early to avoid making the block worse. Try again later.")
            break

        if looks_like_subtitles_disabled(err):
            mark_permanent_skip(video, err)
            print(f"  SKIP  {date}  {title} (subtitles disabled — marked)")
            print(f"         → {err}")
            skipped.append(vid)
            time.sleep(SLEEP_BETWEEN_TRANSCRIPTS_SEC)
            continue

        # No transcript available is common; treat as a skip (with reason) not a fatal error.
        print(f"  SKIP  {date}  {title} (no transcript)")
        print(f"         → {err}")
        skipped.append(vid)
        time.sleep(SLEEP_BETWEEN_TRANSCRIPTS_SEC)
        continue

    store_sermon(video, transcript_text)
    print(f"  ✅    {date}  {title}")
    ingested.append(vid)
    time.sleep(SLEEP_BETWEEN_TRANSCRIPTS_SEC)  # be polite to YouTube

    if len(ingested) >= MAX_NEW_PER_RUN:
        print(f"\nReached MAX_NEW_PER_RUN={MAX_NEW_PER_RUN}. Stopping (cron will continue next run).")
        break

print("─" * 60)
print(f"Ingested: {len(ingested)}  |  Skipped: {len(skipped)}  |  Errors: {len(errors)}")

if ingested:
    print(f"\nUploaded to s3://{BUCKET}/transcripts/")
