from concurrent.futures import ThreadPoolExecutor
from helpers.yt_dlp import *
from helpers.whisper_client import *
from queue import Queue
import helpers.database as db

video_urls = ls_channel_videos(
    "https://www.youtube.com/@SardocheLol/videos"
) + ls_channel_videos("https://www.youtube.com/@sardochereplay/videos")

srt_paths = []
failed_urls = []

with db.connect() as db_conn:
    for video_url in video_urls:
        try:
            srt_paths.append(get_ytdlp_content_srt(video_url, db_conn))
        except Exception as e:
            failed_urls.append((video_url, e))

if len(failed_urls) > 0:
    print("Failed URLs:")
    for video_url, exception in failed_urls:
        print(f"  {video_url} : {exception}")
