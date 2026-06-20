import yt_dlp
from pathlib import Path


def get_video_info(video_url: str) -> dict:
    """Extract and return metadata information for a YouTube video without downloading it.

    Args:
        video_url: URL of the YouTube video to fetch information from

    Returns:
        Dictionary containing video metadata such as title, duration, uploader,
        description, thumbnail URL, formats available, and other video details
        provided by yt_dlp.
    """
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        return ydl.extract_info(video_url, download=False)


def get_video_mp3(
    video_url: str,
    video_info: dict | None = None,
    output_dir: str = ".",
    filename_tmpl: str = "{id}",
) -> Path:
    """Download a YouTube video as an MP3 audio file.

    Args:
        video_url: URL of the YouTube video to download
        video_info: Optional pre-fetched video metadata (will be fetched if not provided)
        output_dir: Directory where the MP3 will be saved (default: current directory)
        filename_tmpl: Template for output filename using video info placeholders

    Returns:
        Path to the downloaded MP3 file
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

    mp3_path = Path(f"{output_dir}/{name}.mp3")
    if not mp3_path.is_file():
        with yt_dlp.YoutubeDL(
            {
                "quiet": True,
                "format": "bestaudio/best",
                "outtmpl": f"{output_dir}/{name}",
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
    output_dir: str = ".",
    filename_tmpl: str = "{date}_{publisher}_{title}",
) -> Path:
    """Download subtitles for a YouTube video as an SRT file.

    Args:
        video_url: URL of the YouTube video
        video_info: Optional pre-fetched video metadata (will be fetched if not provided)
        lang: Language code for subtitles (default: "fr")
        output_dir: Directory where the SRT will be saved (default: current directory)
        filename_tmpl: Template for output filename using video info placeholders

    Returns:
        Path to the downloaded SRT file

    Raises:
        ValueError: If no subtitles are available for the requested language
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

    srt_path = Path(f"{output_dir}/{name}.{lang}.srt")

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
                    "outtmpl": f"{output_dir}/{name}",
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
                    "outtmpl": f"{output_dir}/{name}",
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
    """List all video URLs from a YouTube channel's videos page.

    Args:
        channel_url: URL of the YouTube channel

    Returns:
        List of video URLs from the channel's videos tab
    """
    with yt_dlp.YoutubeDL(
        {
            "quiet": True,
            "extract_flat": True,
        }
    ) as ydl:
        info = ydl.extract_info(f"{channel_url}/videos", download=False)
        return [entry["url"] for entry in info["entries"]]
