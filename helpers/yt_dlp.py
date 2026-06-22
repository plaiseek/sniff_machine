import json
import os
import yt_dlp
from pathlib import Path


def video_url_to_id(video_url: str):
    """
    Extract the platform-specific video ID from a YouTube or Twitch URL.

    Args:
        video_url (str): Full URL of the video. Must be either:
            - YouTube: `https://www.youtube.com/watch?v=<11-char-id>`
            - Twitch: `https://www.twitch.tv/videos/<10-digit-id>`

    Returns:
        str: Canonical video ID:
            - YouTube: 11-character base62 string (e.g., `'dQw4w9WgXcQ'`)
            - Twitch: `'v'` prefix + 10 digits (e.g., `'v1234567890'`)

    Raises:
        ValueError: If the URL does not match supported platforms.

    Example:
        >>> video_url_to_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        'dQw4w9WgXcQ'
    """
    if video_url.startswith("https://www.youtube.com/watch?v="):
        id_start = video_url.find("watch?v=") + 8
        return video_url[id_start : id_start + 11]
    if video_url.startswith("https://www.twitch.tv/videos/"):
        id_start = video_url.find("videos/") + 7
        return "v" + video_url[id_start : id_start + 10]
    raise ValueError(f"Unsupported video host '{video_url}'")


def get_video_info(
    video_url: str, working_folder: str = ".", force: bool = False
) -> dict:
    """
    Retrieve and cache essential metadata for a video (YouTube/Twitch).

    Attempts to load pre-cached info from disk first. If unavailable or `force=True`,
    downloads fresh metadata using `yt_dlp` and saves it as JSON.

    Args:
        video_url (str): Video URL
        working_folder (str, optional): Root directory for caching. Defaults to `"."`.
        force (bool, optional): If `True`, re-download metadata even if cached version exists.
            Defaults to `False`.

    Returns:
        dict: Minimal video metadata with keys:
            - `'id'`: Video ID
            - `'title'`: Video title
            - `'duration'`: Duration in seconds
            - `'upload_date'`: Upload date as `YYYYMMDD` string
            - `'timestamp'`: Unix timestamp of upload (int)
            - `'uploader'`: Channel/creator name
            - `'language'`: Primary language code (e.g., `"fr"`)
            - `'subtitles_langs'`: List of manual subtitle languages available
            - `'automatic_captions_langs'`: List of auto-generated caption languages

    Raises:
        ValueError: If the retrieved video ID doesn't match the expected one (data corruption risk).

    Example:
        >>> info = get_video_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        >>> print(info["title"], info["duration"])
    """
    video_id = video_url_to_id(video_url)
    info_path = Path(f"{working_folder}/infos/{video_id}.json")

    if info_path.is_file() and not force:
        with open(info_path, "r") as f:
            return json.load(f)
    else:
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            video_info = ydl.extract_info(video_url, download=False)

        if video_id != video_info["id"]:
            raise ValueError(
                f"Retrieved video_id '{video_id}' is different from predicted one '{video_info['id']}' ({video_url})"
            )

        filtered_video_info = {
            k: video_info.get(k)
            for k in [
                "id",
                "title",
                "duration",
                "upload_date",
                "timestamp",
                "uploader",
                "language",
            ]
        }
        filtered_video_info["subtitles_langs"] = list(
            video_info.get("subtitles", dict()).keys()
        )
        filtered_video_info["automatic_captions_langs"] = list(
            video_info.get("automatic_captions", dict()).keys()
        )
        os.makedirs(info_path.parent, exist_ok=True)
        with open(info_path, "w") as f:
            json.dump(filtered_video_info, f)

        return filtered_video_info


def get_video_mp3(video_url: str, working_folder: str = ".") -> Path:
    """
    Download the audio track of a video as MP3 (192 kbps) and cache locally.

    If an MP3 file for this video already exists in `working_folder/audios/`,
    it will be reused (no re-download).

    Args:
        video_url (str): Video URL
        working_folder (str, optional): Root directory for audio cache. Defaults to `"."`.

    Returns:
        Path: Absolute path to the downloaded `.mp3` file.

    Raises:
        Any errors from `yt_dlp` (e.g., network issues, private content).

    Example:
        >>> mp3_path = get_video_mp3("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        >>> print(f"Audio saved at: {mp3_path}")
    """
    video_id = video_url_to_id(video_url)
    mp3_path = Path(f"{working_folder}/audios/{video_id}.mp3")
    if not mp3_path.is_file():
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
            ydl.download([video_url])
    return mp3_path


def get_video_srt(
    video_url: str,
    video_info: dict | None = None,
    lang: str = "fr",
    working_folder: str = ".",
    filename_tmpl: str = "{id}",
) -> Path:
    """
    Download subtitles for a video in `.srt` format (manual → auto-generated fallback).

    Caches subtitle files as `{working_folder}/subtitles/{name}.{lang}.srt`.
    Uses `video_info` to generate filenames and check available languages.
    Prioritizes human-curated subtitles over automatic captions.

    Args:
        video_url (str): Video URL
        video_info (dict | None, optional): Pre-fetched metadata dict from `get_video_info()`.
            If `None`, metadata will be fetched automatically. Defaults to `None`.
        lang (str, optional): Target language code (ISO 639-1, e.g., `"en"`, `"fr"`).
            Defaults to `"fr"`.
        working_folder (str, optional): Root directory for subtitle cache. Defaults to `"."`.
        filename_tmpl (str, optional): Template for output filenames.
            Supports placeholders from `video_info` (e.g., `"{title}_{id}"` → `"My Video_dQw4..."`).
            Defaults to `"{id}"`.

    Returns:
        Path: Absolute path to the downloaded `.srt` file.

    Raises:
        ValueError: If the language is unsupported OR placeholders in `filename_tmpl` are invalid.

    Example:
        >>> srt_path = get_video_srt("https://www.youtube.com/watch?v=dQw4w9WgXcQ", lang="en")
        >>> print(f"Subtitles saved at: {srt_path}")
    """
    if video_info is None:
        video_info = get_video_info(video_url)

    try:
        name = filename_tmpl.format(**video_info)
    except KeyError as e:
        raise ValueError(
            f"Unknown placeholder {e} in filename_template. "
            f"Supported placeholders: {sorted(video_info)}"
        )

    srt_path = Path(f"{working_folder}/subtitles/{name}.{lang}.srt")
    if not srt_path.is_file():
        manual_subs = video_info["subtitles_langs"]
        auto_subs = video_info["automatic_captions_langs"]

        if lang in manual_subs:
            print(f"Downloading manual subtitles for '{lang}'...")
            with yt_dlp.YoutubeDL(
                {
                    "quiet": True,
                    "skip_download": True,
                    "writesubtitles": True,
                    "subtitleslangs": [lang],
                    "subtitlesformat": "srt",
                    "outtmpl": str(srt_path)[: -(5 + len(lang))],
                }
            ) as ydl:
                ydl.download([video_url])
        elif lang in auto_subs:
            print(
                f"Manual subtitles not available for '{lang}', falling back to auto-generated captions..."
            )
            with yt_dlp.YoutubeDL(
                {
                    "quiet": True,
                    "skip_download": True,
                    "writeautomaticsub": True,
                    "subtitleslangs": [lang],
                    "subtitlesformat": "srt",
                    "outtmpl": str(srt_path)[: -(5 + len(lang))],
                }
            ) as ydl:
                ydl.download([video_url])
        else:
            raise ValueError(
                f"No subtitles (manual or auto-generated) found for language '{lang}'. "
                f"Available languages: {set(manual_subs + auto_subs)}"
            )
    return srt_path


def ls_channel_videos(channel_url: str) -> list:
    """
    List all publicly available video URLs from a channel (YouTube/Twitch).

    Uses `yt_dlp`'s flat-listing mode (`extract_flat`) to avoid downloading full metadata.

    Args:
        channel_url (str): Channel URL. Must be one of:
            - YouTube: `https://www.youtube.com/channel/{channel_id}` or
                       `https://www.youtube.com/c/{channel_name}`
            - Twitch: `https://www.twitch.tv/{username}`

    Returns:
        list[str]: List of video URLs (e.g., `"https://www.youtube.com/watch?v=..."`)
                   in chronological order (newest first).

    Raises:
        ValueError/yt_dlp errors if the channel is private, nonexistent, or inaccessible.

    Example:
        >>> videos = ls_channel_videos("https://www.youtube.com/c/LinusTechTips")
        >>> print(f"Found {len(videos)} public videos.")
    """
    with yt_dlp.YoutubeDL(
        {
            "quiet": True,
            "extract_flat": True,
        }
    ) as ydl:
        info = ydl.extract_info(f"{channel_url}/videos", download=False)
        return [entry["url"] for entry in info["entries"]]
