from concurrent.futures import ThreadPoolExecutor, wait
from helpers.ffmpeg import *
from helpers.subtitles import *
from helpers.yt_dlp import *
from helpers.whisper_client import *
from math import log10
from queue import Queue

import helpers.database as db

video_urls = ls_channel_videos("https://www.twitch.tv/troublante_acide/videos")
transcription_queue = Queue()
failed_urls = []
failed_transcriptions = []

# whisper_servers = [
#     f"192.168.0.27:{port}" for port in [8070, 8071, 8072, 8073, 8074, 8075]
# ] + [f"127.0.0.1:{port}" for port in [8070, 8071]]
whisper_servers = [f"127.0.0.1:{port}" for port in [8070, 8071]]
assert_whisper_servers(whisper_servers)


def log_prefix(i):
    n = len(video_urls)
    return f"({i:{1+int(log10(n))}}/{n})"


def transcription_loop(whisper_server):
    with db.connect() as db_conn:
        while True:
            item = transcription_queue.get()
            if item is None:
                transcription_queue.task_done()
                return
            i, content, wav_path = item

            whisper_result_path = db.get_content_file_path(
                db_conn, content.id, "whisper_result"
            )
            in_db = whisper_result_path is not None
            if in_db and whisper_result_path.is_file():
                print(f"{log_prefix(i)} Already Done.")
                transcription_queue.task_done()
                continue

            print(f"{log_prefix(i)} Transcripting {wav_path}...")
            try:
                if not in_db:
                    whisper_result_path = Path(
                        f"cache/whisper_result/{content.platform}_{content.external_id}.json"
                    )
                mp3_to_whisper_result(
                    whisper_server, wav_path, whisper_result_path, "fr"
                )
                if not in_db:
                    db.add_content_file(
                        db_conn, whisper_result_path, content.id, "whisper_result"
                    )
                print(f"{log_prefix(i)} Done! -> {whisper_result_path}")
            except Exception as e:
                print(f"{log_prefix(i)} Failed to transcribe '{wav_path}':\n{e}")
                failed_transcriptions.append(wav_path)

            transcription_queue.task_done()


with ThreadPoolExecutor() as consumer_pool:
    futures = [consumer_pool.submit(transcription_loop, s) for s in whisper_servers]

    with db.connect() as db_conn:
        for i, video_url in enumerate(video_urls):
            try:
                print(f"{log_prefix(i)} Fetching {video_url}...")
                content = get_ytdlp_content(db_conn, video_url)
                mp3_path = get_ytdlp_content_mp3(db_conn, video_url, content)

                wav_path = db.get_content_file_path(db_conn, content.id, "16k_mono_wav")
                if wav_path is None:
                    wav_path = Path(
                        f"cache/16k_mono_wav/{content.platform}_{content.external_id}.wav"
                    )
                    convert_to_16k_mono_wav(mp3_path, wav_path, verbose=False)
                    db.add_content_file(db_conn, wav_path, content.id, "16k_mono_wav")

                transcription_queue.put((i, content, mp3_path))
            except Exception as e:
                print(f"Failed to download audio for '{video_url}':\n{e}")
                failed_urls.append(video_url)

    for _ in whisper_servers:
        transcription_queue.put(None)
    wait(futures)
