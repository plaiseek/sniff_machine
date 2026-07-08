import datetime
import json
import os
import yt_dlp
from pathlib import Path

import helpers.database as db


def add_ytdlp_content(
    db_conn: db.sqlite3.Connection,
    url: str,
    force: bool = False,
) -> db.Content:
    content = db.get_content_from_url(db_conn, url)
    if content is not None:
        info_path = db.get_content_file_path(db_conn, content.id, "ytdlp_info")
        if info_path is not None and not force:
            return content

    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        info = ydl.extract_info(url, download=False)

        with open("logs", "w") as f:
            json.dump(ydl.sanitize_info(info), f)

        if content is None:
            content = db.add_content(
                db_conn,
                info["extractor_key"],
                info["id"],
                url,
                info["title"],
                info["duration"],
                info["uploader"],
                datetime.datetime.fromtimestamp(
                    info["timestamp"], tz=datetime.timezone.utc
                ).isoformat(),
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

    mp3_path = db.get_content_file_path(db_conn, content.id, "audio")
    if mp3_path is not None and not force:
        return mp3_path

    if mp3_path is None:
        mp3_path = Path(f"cache/audio/{content.platform}_{content.external_id}.mp3")

    with yt_dlp.YoutubeDL(
        {
            "quiet": True,
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
        db.add_content_file(db_conn, mp3_path, content.id, "audio")
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

    srt_path = db.get_content_file_path(db_conn, content.id, "mp3")
    if srt_path is not None and not force:
        return srt_path

    if srt_path is None:
        srt_path = Path(
            f"cache/subtitles/{content.platform}_{content.external_id}.{lang}.srt"
        )
    with yt_dlp.YoutubeDL(
        {
            "quiet": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": automatic_sub,
            "subtitleslangs": [lang],
            "subtitlesformat": "srt",
            "outtmpl": str(srt_path)[: -(5 + len(lang))],
        }
    ) as ydl:
        ydl.download([url])

    if not srt_path.is_file():
        raise ValueError("It seems there are no subtitles for '{lang}'")

    db.add_content_file(db_conn, srt_path, content.id, "subtitles")
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
