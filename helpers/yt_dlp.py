import json
import os
import yt_dlp
from pathlib import Path


def video_url_to_id(video_url: str):
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
    video_id = video_url_to_id(video_url)
    info_path = Path(f"{working_folder}/infos/{video_id}.json")

    if info_path.is_file() and not force:
        with open(info_path) as f:
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
        manual_subs = video_info.get("subtitles", {})
        auto_subs = video_info.get("automatic_captions", {})

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
                    "outtmpl": f"{working_folder}/{name}",
                }
            ) as ydl:
                ydl.download([video_url])
        else:
            available = sorted(set(manual_subs) | set(auto_subs))
            raise ValueError(
                f"No subtitles (manual or auto-generated) found for language '{lang}'. "
                f"Available languages: {available}"
            )
    return srt_path


def ls_channel_videos(channel_url: str) -> list:
    with yt_dlp.YoutubeDL(
        {
            "quiet": True,
            "extract_flat": True,
        }
    ) as ydl:
        info = ydl.extract_info(f"{channel_url}/videos", download=False)
        return [entry["url"] for entry in info["entries"]]
