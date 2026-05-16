"""
Pulpit - Ingestion Lambda

Serverless sermon ingestion for scheduled EventBridge runs, SQS-backed admin
manual runs, and controlled backfills. Staff can start archive ingest jobs from
the browser without local AWS credentials or a local machine.
"""

import json
import os
import re
import time
from datetime import datetime, timezone

import boto3
import requests
from botocore.exceptions import ClientError

s3 = boto3.client("s3")
ssm = boto3.client("ssm")
bedrock = boto3.client("bedrock-runtime")

BUCKET = os.environ["TRANSCRIPT_BUCKET"]
CHANNEL_ID = os.environ["YOUTUBE_CHANNEL_ID"]
SSM_KEY = os.environ["SSM_PARAMETER_NAME"]
UPLOADS_PLAYLIST = CHANNEL_ID.replace("UC", "UU", 1)

NOVA_LITE_ID = "amazon.nova-lite-v1:0"
TITAN_EMBED_ID = "amazon.titan-embed-text-v2:0"

CHUNK_WORDS = int(os.environ.get("PULPIT_CHUNK_WORDS", 700))
CHUNK_OVERLAP_WORDS = int(os.environ.get("PULPIT_CHUNK_OVERLAP_WORDS", 120))

SKIP_KEYWORDS = [
    "#shorts", "교회소식", "하이라이트", "간증 영상", "소풍", "수련회",
    "달란트", "Lock-In", "lock-in", "환영인사", "감사의 말씀ㅣ", "소개",
]


def lambda_handler(event, context):
    event = event or {}

    if is_sqs_event(event):
        results = []
        for record in event.get("Records", []):
            payload = decode_sqs_body(record)
            results.append(run_ingest_job(payload))
        return {"mode": "sqs", "jobs": results}

    return run_ingest_job(event)


def run_ingest_job(event):
    config = run_config(event)

    if config["backfill"]:
        return run_backfill()

    api_key = get_youtube_api_key()
    videos = get_videos(api_key, config["year_filter"])
    print(f"Found {len(videos)} videos from {config['year_filter']}+")

    ingested = []
    skipped = []
    transcript_attempts = 0
    consecutive_exists = 0

    for video in videos:
        vid = video["id"]
        title = video["title"][:70]
        date = video["published_at"][:10]

        if transcript_exists(video):
            print(f"EXIST {date} {title}")
            skipped.append(vid)
            consecutive_exists += 1
            if consecutive_exists >= config["consecutive_exist_stop"]:
                print("Archive appears caught up. Stopping.")
                break
            continue

        consecutive_exists = 0

        if "이혜진" not in video["title"]:
            print(f"SKIP {date} {title} (not senior pastor)")
            skipped.append(vid)
            continue

        if any(keyword.lower() in video["title"].lower() for keyword in SKIP_KEYWORDS):
            print(f"SKIP {date} {title} (non-sermon)")
            skipped.append(vid)
            continue

        if transcript_attempts >= config["max_transcript_attempts"]:
            print("Transcript attempt cap reached. Stopping.")
            break

        transcript_text, err = fetch_transcript(vid)
        transcript_attempts += 1

        if not transcript_text:
            print(f"SKIP {date} {title} (no transcript: {err})")
            skipped.append(vid)
            time.sleep(config["sleep_sec"])
            continue

        key = store_sermon(video, transcript_text)
        print(f"INGESTED {date} {title} -> s3://{BUCKET}/{key}")
        ingested.append(vid)
        time.sleep(config["sleep_sec"])

        if len(ingested) >= config["max_new_per_run"]:
            print("Max new sermon cap reached. Stopping.")
            break

    if ingested:
        rebuild_index()

    result = {
        "trigger": event.get("trigger", "schedule"),
        "requested_by": event.get("requested_by", "schedule"),
        "ingested": ingested,
        "skipped": skipped,
        "transcript_attempts": transcript_attempts,
        "config": config,
    }
    print(json.dumps(result, ensure_ascii=False))
    return result


def is_sqs_event(event):
    records = event.get("Records") or []
    return bool(records) and records[0].get("eventSource") == "aws:sqs"


def decode_sqs_body(record):
    try:
        body = json.loads(record.get("body") or "{}")
        return body if isinstance(body, dict) else {}
    except json.JSONDecodeError:
        print("SQS message body was not valid JSON")
        return {}


def run_config(event):
    return {
        "year_filter": int(event.get("year_filter") or os.environ.get("PULPIT_YEAR_FILTER", datetime.now().year)),
        "max_new_per_run": int(event.get("max_new_per_run") or os.environ.get("PULPIT_MAX_NEW_PER_RUN", 3)),
        "max_transcript_attempts": int(event.get("max_transcript_attempts") or os.environ.get("PULPIT_MAX_TRANSCRIPT_ATTEMPTS", 6)),
        "consecutive_exist_stop": int(event.get("consecutive_exist_stop") or os.environ.get("PULPIT_CONSECUTIVE_EXIST_STOP", 20)),
        "sleep_sec": float(event.get("sleep_sec") or os.environ.get("PULPIT_SLEEP_SEC", 2.5)),
        "backfill": bool_value(event.get("backfill", False)),
    }


def bool_value(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def get_youtube_api_key():
    """Fetch API key from SSM at runtime - never stored in code or env vars."""
    response = ssm.get_parameter(Name=SSM_KEY, WithDecryption=True)
    return response["Parameter"]["Value"]


def get_videos(api_key, year_filter):
    """Fetch videos from the channel uploads playlist with pagination."""
    videos = []
    page_token = None

    while True:
        params = {
            "part": "snippet",
            "playlistId": UPLOADS_PLAYLIST,
            "maxResults": 50,
            "key": api_key,
        }
        if page_token:
            params["pageToken"] = page_token

        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/playlistItems",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        hit_old = False
        for item in data.get("items", []):
            snippet = item["snippet"]
            date = snippet["publishedAt"][:10]
            if int(date[:4]) < year_filter:
                hit_old = True
                break

            videos.append({
                "id": snippet["resourceId"]["videoId"],
                "title": snippet["title"],
                "description": snippet.get("description", ""),
                "published_at": snippet["publishedAt"],
            })

        page_token = data.get("nextPageToken")
        if hit_old or not page_token:
            return add_video_durations(api_key, videos)


def add_video_durations(api_key, videos):
    duration_by_video = get_video_durations(api_key, [video["id"] for video in videos])
    for video in videos:
        duration = duration_by_video.get(video["id"], {})
        video.update(duration)
    return videos


def get_video_durations(api_key, video_ids):
    durations = {}
    unique_ids = [video_id for video_id in dict.fromkeys(video_ids) if video_id]

    for start in range(0, len(unique_ids), 50):
        batch = unique_ids[start:start + 50]
        params = {
            "part": "contentDetails",
            "id": ",".join(batch),
            "maxResults": 50,
            "key": api_key,
        }
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        for item in resp.json().get("items", []):
            duration_iso = item.get("contentDetails", {}).get("duration", "")
            durations[item["id"]] = {
                "duration": duration_iso,
                "duration_seconds": parse_youtube_duration(duration_iso),
            }

    return durations


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


def fetch_transcript(video_id):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)
        try:
            transcript = transcript_list.find_transcript(["ko", "en"])
        except Exception:
            transcript = next(iter(transcript_list))

        segments = transcript.fetch()
        return " ".join(segment_text(seg) for seg in segments), None
    except Exception as exc:
        msg = " ".join(str(exc).split()) or exc.__class__.__name__
        return None, msg


def segment_text(segment):
    if hasattr(segment, "text"):
        return segment.text
    return segment.get("text", "")


def transcript_exists(video):
    year = video["published_at"][:4]
    return object_exists(f"transcripts/{year}/{video['id']}.json")


def extract_metadata(title, transcript):
    prompt = f"""Extract metadata from this Korean sermon. Return ONLY valid JSON.

Sermon title: {title}
Transcript excerpt: {transcript[:3500]}

Return:
{{
  "pastor_name": "name extracted from title, or empty string",
  "summary": "one sentence archive summary",
  "topics": ["topic1", "topic2"],
  "key_themes": ["theme1", "theme2"],
  "scripture_references": ["Book Chapter:Verse"],
  "related_questions": ["question a listener might ask"]
}}"""

    try:
        resp = bedrock.converse(
            modelId=NOVA_LITE_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 600},
        )
        text = resp["output"]["message"]["content"][0]["text"].strip()
        return json.loads(clean_model_json(text))
    except Exception as exc:
        print(f"Metadata extraction failed: {exc}")
        return {}


def clean_model_json(text):
    if "```" not in text:
        return text.strip()

    parts = text.split("```")
    text = parts[1] if len(parts) > 1 else parts[0]
    if text.startswith("json"):
        text = text[4:]
    return text.strip()


def generate_embedding(text):
    try:
        resp = bedrock.invoke_model(
            modelId=TITAN_EMBED_ID,
            body=json.dumps({
                "inputText": text[:8000],
                "dimensions": 256,
                "normalize": True,
            }),
        )
        return json.loads(resp["body"].read())["embedding"]
    except Exception as exc:
        print(f"Embedding failed: {exc}")
        return None


def store_sermon(video, transcript_text):
    year = video["published_at"][:4]
    metadata = extract_metadata(video["title"], transcript_text)
    embedding = generate_embedding(transcript_text)

    sermon = {
        "sermon_id": video["id"],
        "title": video["title"],
        "date": video["published_at"][:10],
        "youtube_url": f"https://youtube.com/watch?v={video['id']}",
        "duration": video.get("duration", ""),
        "duration_seconds": safe_int(video.get("duration_seconds")),
        "description": video.get("description", "")[:500],
        "transcript": transcript_text,
        "pastor_name": metadata.get("pastor_name", ""),
        "summary": metadata.get("summary", ""),
        "topics": metadata.get("topics", []),
        "key_themes": metadata.get("key_themes", []),
        "scripture_references": metadata.get("scripture_references", []),
        "related_questions": metadata.get("related_questions", []),
        "embedding": embedding,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }

    key = f"transcripts/{year}/{sermon['sermon_id']}.json"
    put_json(key, sermon)
    write_sermon_artifacts(sermon)
    return key


def write_sermon_artifacts(sermon):
    sermon_id = sermon.get("sermon_id")
    if not sermon_id:
        return []

    card = build_sermon_card(sermon)
    chunks = build_chunks(sermon)

    put_json(f"sermons/{sermon_id}/card.json", card)
    put_json(
        f"sermons/{sermon_id}/chunks.json",
        {
            "sermon_id": sermon_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "chunk_count": len(chunks),
            "chunks": chunks,
        },
    )
    return chunks


def build_sermon_card(sermon):
    return {
        "sermon_id": sermon.get("sermon_id", ""),
        "title": sermon.get("title", ""),
        "date": sermon.get("date", ""),
        "youtube_url": sermon.get("youtube_url", ""),
        "duration": sermon.get("duration", ""),
        "duration_seconds": safe_int(sermon.get("duration_seconds")),
        "pastor_name": sermon.get("pastor_name", ""),
        "summary": sermon.get("summary", ""),
        "topics": sermon.get("topics", []),
        "key_themes": sermon.get("key_themes", []),
        "scripture_references": sermon.get("scripture_references", []),
        "related_questions": sermon.get("related_questions", []),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_chunks(sermon):
    transcript = sermon.get("transcript", "")
    words = transcript.split()
    if not words:
        return []

    sermon_id = sermon.get("sermon_id", "")
    chunks = []
    step = max(1, CHUNK_WORDS - CHUNK_OVERLAP_WORDS)

    for chunk_index, start in enumerate(range(0, len(words), step)):
        end = min(start + CHUNK_WORDS, len(words))
        chunk_text = " ".join(words[start:end]).strip()
        if not chunk_text:
            continue

        chunk = {
            "chunk_id": f"{sermon_id}:{chunk_index:04d}",
            "sermon_id": sermon_id,
            "chunk_index": chunk_index,
            "start_word": start,
            "end_word": end,
            "title": sermon.get("title", ""),
            "date": sermon.get("date", ""),
            "youtube_url": sermon.get("youtube_url", ""),
            "duration": sermon.get("duration", ""),
            "duration_seconds": safe_int(sermon.get("duration_seconds")),
            "pastor_name": sermon.get("pastor_name", ""),
            "topics": sermon.get("topics", []),
            "key_themes": sermon.get("key_themes", []),
            "scripture_references": sermon.get("scripture_references", []),
            "related_questions": sermon.get("related_questions", []),
            "summary": sermon.get("summary", ""),
            "text": chunk_text,
            "embedding": generate_embedding(chunk_text),
        }
        chunks.append(chunk)

        if end == len(words):
            break

    return chunks


def rebuild_index():
    print("Rebuilding transcripts/index.json and indexes/chunk-index.json")
    sermon_entries = []
    chunk_entries = []

    for key in list_sermon_keys():
        sermon = load_json(key)
        if not sermon:
            continue

        sermon_entries.append(build_sermon_index_entry(sermon))

        artifact = load_json(f"sermons/{sermon.get('sermon_id')}/chunks.json")
        chunks = artifact.get("chunks", []) if isinstance(artifact, dict) else []
        if chunks:
            chunk_entries.extend(chunks)
        else:
            legacy_chunk = build_legacy_chunk_entry(sermon)
            if legacy_chunk:
                chunk_entries.append(legacy_chunk)

    sermon_entries.sort(key=lambda entry: entry.get("date", ""))
    chunk_entries.sort(key=lambda entry: (
        entry.get("date", ""),
        entry.get("sermon_id", ""),
        entry.get("chunk_index", 0),
    ))

    now = datetime.now(timezone.utc).isoformat()
    put_json("transcripts/index.json", {
        "generated_at": now,
        "index_type": "sermon",
        "sermon_count": len(sermon_entries),
        "embedding_count": sum(1 for entry in sermon_entries if entry.get("embedding")),
        "sermons": sermon_entries,
    })
    put_json("indexes/chunk-index.json", {
        "generated_at": now,
        "index_type": "chunk",
        "sermon_count": len({entry.get("sermon_id") for entry in chunk_entries}),
        "chunk_count": len(chunk_entries),
        "embedding_count": sum(1 for entry in chunk_entries if entry.get("embedding")),
        "chunks": chunk_entries,
    })


def build_sermon_index_entry(sermon):
    return {
        "sermon_id": sermon.get("sermon_id", ""),
        "title": sermon.get("title", ""),
        "date": sermon.get("date", ""),
        "youtube_url": sermon.get("youtube_url", ""),
        "duration": sermon.get("duration", ""),
        "duration_seconds": safe_int(sermon.get("duration_seconds")),
        "pastor_name": sermon.get("pastor_name", ""),
        "summary": sermon.get("summary", ""),
        "topics": sermon.get("topics", []),
        "key_themes": sermon.get("key_themes", []),
        "scripture_references": sermon.get("scripture_references", []),
        "related_questions": sermon.get("related_questions", []),
        "transcript": sermon.get("transcript", "")[:2000],
        "embedding": sermon.get("embedding"),
    }


def build_legacy_chunk_entry(sermon):
    text = sermon.get("transcript", "")[:2500].strip()
    if not text:
        return None

    sermon_id = sermon.get("sermon_id", "")
    return {
        "chunk_id": f"{sermon_id}:legacy:0000",
        "sermon_id": sermon_id,
        "chunk_index": 0,
        "legacy": True,
        "title": sermon.get("title", ""),
        "date": sermon.get("date", ""),
        "youtube_url": sermon.get("youtube_url", ""),
        "duration": sermon.get("duration", ""),
        "duration_seconds": safe_int(sermon.get("duration_seconds")),
        "pastor_name": sermon.get("pastor_name", ""),
        "summary": sermon.get("summary", ""),
        "topics": sermon.get("topics", []),
        "key_themes": sermon.get("key_themes", []),
        "scripture_references": sermon.get("scripture_references", []),
        "related_questions": sermon.get("related_questions", []),
        "text": text,
        "embedding": sermon.get("embedding"),
    }


def run_backfill():
    updated = 0
    skipped = 0
    artifacts_written = 0
    keys = list_sermon_keys()
    duration_by_video = {}

    try:
        api_key = get_youtube_api_key()
        duration_by_video = get_video_durations(api_key, [
            key.rsplit("/", 1)[-1].replace(".json", "")
            for key in keys
        ])
    except Exception as exc:
        print(f"Duration metadata backfill skipped: {exc}")

    for key in keys:
        sermon = load_json(key)
        if not sermon:
            skipped += 1
            continue

        transcript = sermon.get("transcript", "")
        if not transcript:
            skipped += 1
            continue

        changed = False
        if metadata_fields_missing(sermon):
            metadata = extract_metadata(sermon.get("title", ""), transcript)
            sermon.update({
                "pastor_name": metadata.get("pastor_name", sermon.get("pastor_name", "")),
                "summary": metadata.get("summary", sermon.get("summary", "")),
                "topics": metadata.get("topics", sermon.get("topics", [])),
                "key_themes": metadata.get("key_themes", sermon.get("key_themes", [])),
                "scripture_references": metadata.get("scripture_references", sermon.get("scripture_references", [])),
                "related_questions": metadata.get("related_questions", sermon.get("related_questions", [])),
            })
            changed = True

        if not sermon.get("embedding"):
            sermon["embedding"] = generate_embedding(transcript)
            changed = True

        duration = duration_by_video.get(sermon.get("sermon_id", ""))
        if duration:
            duration_seconds = safe_int(duration.get("duration_seconds"))
            if sermon.get("duration") != duration.get("duration") or safe_int(sermon.get("duration_seconds")) != duration_seconds:
                sermon["duration"] = duration.get("duration", "")
                sermon["duration_seconds"] = duration_seconds
                changed = True

        if changed:
            put_json(key, sermon)
            updated += 1

        if artifacts_missing(sermon):
            write_sermon_artifacts(sermon)
            artifacts_written += 1

    rebuild_index()
    return {
        "mode": "backfill",
        "updated": updated,
        "artifacts_written": artifacts_written,
        "skipped": skipped,
    }


def metadata_fields_missing(sermon):
    return not (
        sermon.get("pastor_name")
        and sermon.get("summary")
        and sermon.get("topics")
        and sermon.get("key_themes")
    )


def artifacts_missing(sermon):
    sermon_id = sermon.get("sermon_id")
    if not sermon_id:
        return False
    return not (
        object_exists(f"sermons/{sermon_id}/card.json")
        and object_exists(f"sermons/{sermon_id}/chunks.json")
    )


def list_sermon_keys():
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix="transcripts/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if "/skips/" in key or key == "transcripts/index.json" or not key.endswith(".json"):
                continue
            keys.append(key)
    return keys


def object_exists(key):
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 404:
            return False
        code = exc.response.get("Error", {}).get("Code")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def load_json(key):
    try:
        raw = s3.get_object(Bucket=BUCKET, Key=key)
        return json.loads(raw["Body"].read())
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"NoSuchKey", "404", "NotFound"}:
            return None
        raise


def put_json(key, body):
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(body, ensure_ascii=False),
        ContentType="application/json",
    )
