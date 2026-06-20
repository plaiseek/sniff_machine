from concurrent.futures import ThreadPoolExecutor
from helpers.yt_dlp import *
from helpers.whisper_client import *
from queue import Queue

video_urls = ls_channel_videos("https://www.twitch.tv/medoliie/videos")


def download_mp3(video_url):
    try:
        mp3_path = get_video_mp3(video_url, output_dir="cache/medoliie_mp3")
        return (video_url, mp3_path)
    except Exception as e:
        print(f"Failed to download audio for '{video_url}':\n{e}")
        return (video_url, None)


with ThreadPoolExecutor(max_workers=4) as executor:
    mp3_results = dict(executor.map(download_mp3, video_urls))


transcription_queue = Queue()
for item in mp3_results.items():
    transcription_queue.put(item)
transcription_results = Queue()
whisper_servers = [f"http://127.0.0.1:{port}/inference" for port in [8070, 8071]] + [
    f"http://192.168.0.27:{port}/inference"
    for port in [8072, 8073, 8074, 8075, 8076, 8077]
]


def transcription_loop(whisper_server):
    while transcription_queue.qsize() > 0:
        video_url, mp3_path = transcription_queue.get()

        print(f"Transcripting {mp3_path}.")
        try:
            srt_path = mp3_to_srt(mp3_path, "cache/medolie_srt", whisper_server)
            transcription_results.put((video_url, srt_path))
        except Exception as e:
            print(f"Failed to download audio for '{video_url}':\n{e}")
            transcription_results.put((video_url, None))

        transcription_queue.task_done()


with ThreadPoolExecutor(max_workers=len(whisper_servers)) as executor:
    results = list(executor.map(transcription_loop, whisper_servers))
