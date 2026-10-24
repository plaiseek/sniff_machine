from helpers.ffmpeg import *
from helpers.subtitles import *
from helpers.yt_dlp import *
from helpers.whisper_client import *
from math import log10
from queue import Empty, Queue

import helpers.database as db
import requests
import sys
import threading

whisper_servers = [
    f"192.168.0.41:{port}" for port in [8070, 8071, 8072, 8073, 8074, 8075]
] + [f"127.0.0.1:{port}" for port in [8070, 8071]]
# whisper_servers = [f"127.0.0.1:{port}" for port in [8070, 8071]]
assert_whisper_servers(whisper_servers)

video_urls = list(
    dict.fromkeys(ls_channel_videos("https://www.youtube.com/@psyhodelik/videos"))
)
# video_urls = ["https://www.youtube.com/watch?v=2PxDP6yLg2w"]
transcription_queue = Queue()
downloads_done = threading.Event()
failed_urls = []
failed_transcriptions = []


def log_prefix(i):
    n = len(video_urls)
    return f"({i:{1+int(log10(n))}}/{n})"


def transcription_loop(whisper_server):
    with db.connect() as db_conn:
        while True:
            try:
                item = transcription_queue.get(timeout=1)
            except Empty:
                # Stop once downloads are over and no other worker can still hand an item back
                if (
                    downloads_done.is_set()
                    and transcription_queue.unfinished_tasks == 0
                ):
                    return
                continue
            i, content, wav_path = item

            try:
                print(f"{log_prefix(i)} [{whisper_server}] Transcribing {wav_path}...")
                whisper_result_path = Path(
                    f"cache/whisper_result/{content.platform}_{content.external_id}.json"
                )
                wav_to_whisper_result(
                    whisper_server, wav_path, whisper_result_path, "fr"
                )
                db.add_content_file(
                    db_conn, whisper_result_path, content.id, "whisper_result"
                )
                print(f"{log_prefix(i)} Done! -> {whisper_result_path}")
            except requests.exceptions.ConnectionError as e:
                # This server is down: hand the item back to the other servers and stop
                print(
                    f"{log_prefix(i)} Whisper server {whisper_server} is unreachable, stopping its worker:\n{e}"
                )
                transcription_queue.put(item)
                return
            except Exception as e:
                print(f"{log_prefix(i)} Failed to transcribe '{wav_path}':\n{e}")
                failed_transcriptions.append((wav_path, e))
            finally:
                # After the hand-back put(): the count never drops to 0 while the item is pending
                transcription_queue.task_done()


# Daemon threads: Ctrl+C stops the script at once instead of waiting for the transcriptions in progress
consumers = [
    threading.Thread(target=transcription_loop, args=(s,), daemon=True)
    for s in whisper_servers
]
for consumer in consumers:
    consumer.start()

try:
    with db.connect() as db_conn:
        queued_content_ids = set()
        for i, video_url in enumerate(video_urls, 1):
            try:
                print(f"{log_prefix(i)} Fetching {video_url}...")
                content = get_ytdlp_content(db_conn, video_url)
                if content.id in queued_content_ids:
                    continue
                if db.get_existing_content_file(db_conn, content.id, "whisper_result"):
                    print(f"{log_prefix(i)} Already Done.")
                    queued_content_ids.add(content.id)
                    continue

                # The mp3 is only needed to (re)build the wav: it can be deleted once the wav exists
                wav_path = db.get_existing_content_file(
                    db_conn, content.id, "16k_mono_wav"
                )
                if wav_path is None:
                    mp3_path = get_ytdlp_content_mp3(db_conn, video_url, content)
                    wav_path = Path(
                        f"cache/16k_mono_wav/{content.platform}_{content.external_id}.wav"
                    )
                    convert_to_16k_mono_wav(mp3_path, wav_path, verbose=False)
                    db.add_content_file(db_conn, wav_path, content.id, "16k_mono_wav")

                queued_content_ids.add(content.id)
                transcription_queue.put((i, content, wav_path))
            except Exception as e:
                print(
                    f"{log_prefix(i)} Failed to download audio for '{video_url}':\n{e}"
                )
                failed_urls.append((video_url, e))
finally:
    downloads_done.set()

for consumer in consumers:
    consumer.join()

# Items handed back by a worker whose Whisper server became unreachable, with no worker left to take them
not_transcribed = []
while True:
    try:
        not_transcribed.append(transcription_queue.get_nowait()[2])
    except Empty:
        break

if len(failed_urls) > 0:
    print("Failed downloads:")
    for video_url, exception in failed_urls:
        print(f"  {video_url} : {exception}")
if len(failed_transcriptions) > 0:
    print("Failed transcriptions:")
    for wav_path, exception in failed_transcriptions:
        print(f"  {wav_path} : {exception}")
if len(not_transcribed) > 0:
    print("Not transcribed, a Whisper server became unreachable (re-run to retry):")
    for wav_path in not_transcribed:
        print(f"  {wav_path}")
if failed_urls or failed_transcriptions or not_transcribed:
    sys.exit(1)
