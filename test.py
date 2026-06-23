from helpers.subtitles import *
from helpers.yt_dlp import *
from helpers.whisper_client import *

video_urls = ls_channel_videos("https://www.france.tv/france-2/journal-20h00")

print(video_urls)

# video_url = "https://www.france.tv/france-2/journal-20h00/8550671-edition-du-lundi-22-juin-2026.html"

# info = get_video_info(video_url)
# print(info)

# mp3_path = get_video_mp3(video_url, working_folder="cache/test")

# srt_path = mp3_to_srt(
#     mp3_path, "http://127.0.0.1:8070/inference", "cache/test/subtitles", "fr", force=True
# )
# txt_path = srt_to_transcription(srt_path, f"cache/test/transcripts")
# print(load_sparsified_transcription(txt_path, 0))