from concurrent.futures import ThreadPoolExecutor
from helpers.yt_dlp import *
from helpers.whisper_client import *
from queue import Queue

video_urls = ls_channel_videos("https://www.youtube.com/@SardocheLol/videos") + ls_channel_videos("https://www.youtube.com/@sardochereplay/videos")


def download_srt(video_url):
    try:
        srt_path = get_video_srt(video_url, working_folder="cache/sardoche")
        return (video_url, srt_path)
    except Exception as e:
        print(f"Failed to download subtitles for '{video_url}':\n{e}")
        return (video_url, None)


with ThreadPoolExecutor(max_workers=4) as executor:
    srt_results = dict(executor.map(download_srt, video_urls))

