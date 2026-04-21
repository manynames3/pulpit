"""
Pulpit — Ingestion Lambda
Runs weekly via EventBridge. Fetches new sermon transcripts from YouTube
and stores them in S3 for Bedrock Knowledge Base ingestion.

Why YouTube transcripts instead of AWS Transcribe:
- YouTube auto-generates free captions for virtually all uploaded videos
- AWS Transcribe costs ~$0.02/min = ~$0.90 per 45-min sermon = ~$47/year
- youtube-transcript-api pulls captions with zero cost and zero quota impact
"""

import json
import os
import re
import boto3
from datetime import datetime, timezone
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi

s3 = boto3.client("s3")

BUCKET          = os.environ["TRANSCRIPT_BUCKET"]
CHANNEL_ID      = os.environ["YOUTUBE_CHANNEL_ID"]
YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]


def lambda_handler(event, context):
    """Entry point — fetch new videos and store transcripts."""
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    new_videos = get_new_videos(youtube)

    ingested = []
    skipped = []

    for video in new_videos:
        video_id = video["id"]

        # Skip if already ingested
        if transcript_exists(video_id):
            skipped.append(video_id)
            continue

        transcript = fetch_transcript(video_id)
        if not transcript:
            skipped.append(video_id)
            continue

        sermon = build_sermon_record(video, transcript)
        store_transcript(sermon)
        ingested.append(video_id)

    print(f"Ingested: {len(ingested)} | Skipped: {len(skipped)}")
    return {"ingested": ingested, "skipped": skipped}


def get_new_videos(youtube, max_results=10):
    """Fetch recent videos from the church channel."""
    response = youtube.search().list(
        part="snippet",
        channelId=CHANNEL_ID,
        order="date",
        type="video",
        maxResults=max_results
    ).execute()

    return [
        {
            "id": item["id"]["videoId"],
            "title": item["snippet"]["title"],
            "description": item["snippet"]["description"],
            "published_at": item["snippet"]["publishedAt"],
            "thumbnail": item["snippet"]["thumbnails"].get("high", {}).get("url", "")
        }
        for item in response.get("items", [])
    ]


def fetch_transcript(video_id):
    """Pull free YouTube captions. Returns None if unavailable."""
    try:
        # Prefer English, fall back to Korean
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        try:
            transcript = transcript_list.find_transcript(["en"])
        except Exception:
            transcript = transcript_list.find_transcript(["ko"])

        segments = transcript.fetch()
        return " ".join([seg["text"] for seg in segments])
    except Exception as e:
        print(f"No transcript for {video_id}: {e}")
        return None


def extract_scripture_references(text):
    """
    Extract Bible references from video description or title.
    Churches almost always include scripture in descriptions.
    Example matches: 'John 3:16', 'Romans 8:28-39', 'Psalm 23'
    """
    pattern = r'\b(?:Genesis|Exodus|Leviticus|Numbers|Deuteronomy|Joshua|Judges|Ruth|' \
              r'Samuel|Kings|Chronicles|Ezra|Nehemiah|Esther|Job|Psalm(?:s)?|Proverbs|' \
              r'Ecclesiastes|Isaiah|Jeremiah|Lamentations|Ezekiel|Daniel|Hosea|Joel|' \
              r'Amos|Obadiah|Jonah|Micah|Nahum|Habakkuk|Zephaniah|Haggai|Zechariah|' \
              r'Malachi|Matthew|Mark|Luke|John|Acts|Romans|Corinthians|Galatians|' \
              r'Ephesians|Philippians|Colossians|Thessalonians|Timothy|Titus|Philemon|' \
              r'Hebrews|James|Peter|Jude|Revelation)\s+\d+(?::\d+(?:-\d+)?)?\b'
    return list(set(re.findall(pattern, text, re.IGNORECASE)))


def build_sermon_record(video, transcript):
    """Structure sermon data with metadata for KB retrieval quality."""
    description = video.get("description", "")
    return {
        "sermon_id": video["id"],
        "title": video["title"],
        "date": video["published_at"][:10],
        "youtube_url": f"https://youtube.com/watch?v={video['id']}",
        "scripture_references": extract_scripture_references(
            video["title"] + " " + description
        ),
        "description": description[:500],
        "language": "en",
        "transcript": transcript,
        "ingested_at": datetime.now(timezone.utc).isoformat()
    }


def transcript_exists(video_id):
    """Check S3 before re-ingesting."""
    try:
        s3.head_object(Bucket=BUCKET, Key=f"transcripts/{video_id}.json")
        return True
    except Exception:
        return False


def store_transcript(sermon):
    """Write sermon JSON to S3."""
    key = f"transcripts/{sermon['date'][:4]}/{sermon['sermon_id']}.json"
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(sermon, ensure_ascii=False),
        ContentType="application/json"
    )
    print(f"Stored: {key}")
