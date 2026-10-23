import datetime
import json
import os
import yt_dlp
from pathlib import Path

import helpers.database as db


def _upload_date(info: dict) -> str:
    """ISO-8601 upload date from yt-dlp info, or '' when the extractor gives none."""
    timestamp = info.get("timestamp") or info.get("release_timestamp")
    if timestamp is not None:
        return datetime.datetime.fromtimestamp(
            timestamp, tz=datetime.timezone.utc
        ).isoformat()
    date = info.get("upload_date") or info.get("release_date")  # YYYYMMDD
    if date:
        return f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    return ""


def add_ytdlp_content(
    db_conn: db.sqlite3.Connection,
    url: str,
    force: bool = False,
) -> db.Content:
    content = db.get_content_from_url(db_conn, url)
    if content is not None and not force:
        if db.get_existing_content_file(db_conn, content.id, "ytdlp_info") is not None:
            return content

    # noplaylist: a watch?v=X&list=Y URL is video X; extract_flat: a real playlist is rejected
    # after one request instead of extracting all its entries first
    with yt_dlp.YoutubeDL(
        {"quiet": True, "noplaylist": True, "extract_flat": "in_playlist"}
    ) as ydl:
        info = ydl.extract_info(url, download=False)

        if info.get("_type") in ("playlist", "multi_video"):
            raise ValueError(f"'{url}' is a playlist, not a single content")
        # A live, upcoming or still recording content (e.g. the Twitch VOD of a running
        # stream) would be cached truncated: it is picked up by a later run instead
        if info.get("is_live") or info.get("live_status") in (
            "is_live",
            "is_upcoming",
            "post_live",
        ):
            raise ValueError(
                f"'{url}' is live or upcoming ({info.get('live_status')}), skipped"
            )

        if content is None:
            # The same content can be reached through several URLs (youtu.be/..., &t=..., embeds)
            content = db.get_content_from_external_id(
                db_conn, info["extractor_key"], info["id"]
            )
            if content is not None and not force:
                if db.get_existing_content_file(db_conn, content.id, "ytdlp_info") is not None:
                    return content

        if content is None:
            # Many extractors leave out uploader, timestamp or duration
            content = db.add_content(
                db_conn,
                info["extractor_key"],
                info["id"],
                url,
                info.get("title") or "",
                int(info.get("duration") or 0),
                info.get("uploader") or info.get("channel") or info.get("series") or "",
                _upload_date(info),
            )

        info_path = Path(
            f"cache/ytdlp_info/{content.platform}_{content.external_id}.json"
        )
        os.makedirs(info_path.parent, exist_ok=True)
        with open(info_path, "w") as f:
            json.dump(ydl.sanitize_info(info), f)
        db.add_content_file(db_conn, info_path, content.id, "ytdlp_info")

        return content


def get_ytdlp_content(db_conn: db.sqlite3.Connection, url: str) -> db.Content:
    content = db.get_content_from_url(db_conn, url)
    if content is None:
        content = add_ytdlp_content(db_conn, url)
    return content


def get_ytdlp_content_mp3(
    db_conn: db.sqlite3.Connection,
    url: str,
    content: db.Content | None = None,
    force: bool = False,
) -> Path:
    if content is None:
        content = get_ytdlp_content(db_conn, url)

    mp3_path = db.get_existing_content_file(db_conn, content.id, "ytdlp_audio")
    if mp3_path is not None and not force:
        return mp3_path

    mp3_path = Path(f"cache/ytdlp_audio/{content.platform}_{content.external_id}.mp3")

    with yt_dlp.YoutubeDL(
        {
            "quiet": True,
            "noplaylist": True,
            "format": "bestaudio/best",
            "outtmpl": str(mp3_path)[:-4],
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
    ) as ydl:
        ydl.download([url])

    if not mp3_path.is_file():
        raise ValueError(f"yt-dlp did not produce '{mp3_path}'")
    db.add_content_file(db_conn, mp3_path, content.id, "ytdlp_audio")
    return mp3_path


def get_ytdlp_content_srt(
    url: str,
    db_conn: db.sqlite3.Connection,
    content: db.Content | None = None,
    lang: str = "fr",
    force: bool = False,
    automatic_sub: bool = False,
) -> Path:
    if content is None:
        content = get_ytdlp_content(db_conn, url)

    # Only one subtitles file per content: the cached one is reused only if it is in `lang`
    srt_path = Path(
        f"cache/ytdlp_subtitles/{content.platform}_{content.external_id}.{lang}.srt"
    )
    cached_path = db.get_existing_content_file(db_conn, content.id, "ytdlp_subtitles")
    if cached_path == srt_path and not force:
        return srt_path

    with yt_dlp.YoutubeDL(
        {
            "quiet": True,
            "noplaylist": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": automatic_sub,
            "subtitleslangs": [lang],
            "subtitlesformat": "srt",
            # Platforms without srt subtitles (e.g. vtt/ttml only) are converted to srt
            "postprocessors": [
                {
                    "key": "FFmpegSubtitlesConvertor",
                    "format": "srt",
                    "when": "before_dl",
                }
            ],
            "outtmpl": str(srt_path)[: -(5 + len(lang))],
        }
    ) as ydl:
        ydl.download([url])

    if not srt_path.is_file():
        raise ValueError(f"It seems there are no subtitles for '{lang}'")

    db.add_content_file(db_conn, srt_path, content.id, "ytdlp_subtitles")
    return srt_path


def ls_channel_videos(channel_url: str) -> list:
    with yt_dlp.YoutubeDL(
        {
            "quiet": True,
            "extract_flat": True,
        }
    ) as ydl:
        info = ydl.extract_info(f"{channel_url}", download=False)
        # print(info)
        return [entry["url"] for entry in info["entries"]]
