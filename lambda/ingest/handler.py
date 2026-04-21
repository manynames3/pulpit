"""
Pulpit — Ingestion Lambda
Runs weekly via EventBridge. Fetches new sermon transcripts from YouTube
and stores them in S3 for Bedrock Knowledge Base ingestion.

Why YouTube transcripts instead of AWS Transcribe:
- YouTube auto-generates free captions for virtually all uploaded videos
- AWS Transcribe costs ~$0.02/min = ~$0.90 per 45-min sermon = ~$47/year
- youtube-transcript-api pulls captions with zero cost and zero quota impact

Why SSM for the API key:
- Secrets must never be hardcoded or committed to git
- SSM SecureString encrypts at rest with KMS
- Lambda IAM role grants access to this specific parameter only
- Rotating the key requires zero code changes — update SSM value only
"""

import json
import os
import re
import boto3
from datetime import datetime, timezone
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi

s3  = boto3.client("s3")
ssm = boto3.client("ssm")

BUCKET     = os.environ["TRANSCRIPT_BUCKET"]
CHANNEL_ID = os.environ["YOUTUBE_CHANNEL_ID"]
SSM_KEY    = os.environ["SSM_PARAMETER_NAME"]


def get_youtube_api_key():
    """Fetch API key from SSM at runtime — never stored in env vars or code."""
    response = ssm.get_parameter(Name=SSM_KEY, WithDecryption=True)
    return response["Parameter"]["Value"]


def lambda_handler(event, context):
    youtube_api_key = get_youtube_api_key()
    youtube = build("youtube", "v3", developerKey=youtube_api_key)
    new_videos = get_new_videos(youtube)

    ingested = []
    skipped  = []

    for video in new_videos:
        video_id = video["id"]

        # Only ingest 2026 and later sermons.
        # Keeps embedding costs minimal during pilot (~$0.10/sermon).
        # To include full archive, remove this check and re-run.
        published_year = int(video["published_at"][:4])
        if published_year < 2026:
            print(f"Skipping pre-2026 video: {video_id} ({video['published_at'][:10]})")
            skipped.append(video_id)
            continue

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
            "id":           item["id"]["videoId"],
            "title":        item["snippet"]["title"],
            "description":  item["snippet"]["description"],
            "published_at": item["snippet"]["publishedAt"],
        }
        for item in response.get("items", [])
    ]


def fetch_transcript(video_id):
    """
    Pull free YouTube captions — no API key needed for this step.
    Prefers English, falls back to Korean for bilingual church content.
    Returns None if no captions available.
    """
    try:
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
    Extract Bible book references from video title and description.
    Churches almost always include scripture references in descriptions.
    """
    pattern = (
        r'\b(?:Genesis|Exodus|Leviticus|Numbers|Deuteronomy|Joshua|Judges|Ruth|'
        r'(?:1|2)\s*Samuel|(?:1|2)\s*Kings|(?:1|2)\s*Chronicles|Ezra|Nehemiah|'
        r'Esther|Job|Psalms?|Proverbs|Ecclesiastes|Isaiah|Jeremiah|Lamentations|'
        r'Ezekiel|Daniel|Hosea|Joel|Amos|Obadiah|Jonah|Micah|Nahum|Habakkuk|'
        r'Zephaniah|Haggai|Zechariah|Malachi|Matthew|Mark|Luke|John|Acts|Romans|'
        r'(?:1|2)\s*Corinthians|Galatians|Ephesians|Philippians|Colossians|'
        r'(?:1|2)\s*Thessalonians|(?:1|2)\s*Timothy|Titus|Philemon|Hebrews|'
        r'James|(?:1|2)\s*Peter|(?:1|2|3)\s*John|Jude|Revelation)'
        r'\s+\d+(?::\d+(?:-\d+)?)?\b'
    )
    return list(set(re.findall(pattern, text, re.IGNORECASE)))


def build_sermon_record(video, transcript):
    description = video.get("description", "")
    return {
        "sermon_id":           video["id"],
        "title":               video["title"],
        "date":                video["published_at"][:10],
        "youtube_url":         f"https://youtube.com/watch?v={video['id']}",
        "scripture_references": extract_scripture_references(
            video["title"] + " " + description
        ),
        "description":         description[:500],
        "language":            "en",
        "transcript":          transcript,
        "ingested_at":         datetime.now(timezone.utc).isoformat()
    }


def transcript_exists(video_id):
    try:
        s3.head_object(Bucket=BUCKET, Key=f"transcripts/{video_id}.json")
        return True
    except Exception:
        return False


def store_transcript(sermon):
    key = f"transcripts/{sermon['date'][:4]}/{sermon['sermon_id']}.json"
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(sermon, ensure_ascii=False),
        ContentType="application/json"
    )
    print(f"Stored: {key}")
