from helpers.ffmpeg import *
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

# Contents with no audio file (e.g. subtitles only) are left out: their audio is never downloaded
with db.connect() as db_conn:
    contents = db.get_contents_without_file(
        db_conn, "whisper_result", ["16k_mono_wav", "ytdlp_audio"]
    )
if len(contents) == 0:
    print("Nothing to transcribe.")
    sys.exit(0)
print(f"{len(contents)} audios to transcribe.")
assert_whisper_servers(whisper_servers)

transcription_queue = Queue()
preparation_done = threading.Event()
failed_preparations = []
failed_transcriptions = []


def log_prefix(i):
    n = len(contents)
    return f"({i:{1+int(log10(n))}}/{n})"


def transcription_loop(whisper_server):
    with db.connect() as db_conn:
        while True:
            try:
                item = transcription_queue.get(timeout=1)
            except Empty:
                # Stop once preparations are over and no other worker can still hand an item back
                if (
                    preparation_done.is_set()
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
        for i, content in enumerate(contents, 1):
            try:
                # A registered wav or mp3 deleted from disk is rebuilt, re-downloading the mp3 if needed
                wav_path = db.get_existing_content_file(
                    db_conn, content.id, "16k_mono_wav"
                )
                if wav_path is None:
                    print(f"{log_prefix(i)} Preparing the wav of {content.url}...")
                    mp3_path = get_ytdlp_content_mp3(db_conn, content.url, content)
                    wav_path = Path(
                        f"cache/16k_mono_wav/{content.platform}_{content.external_id}.wav"
                    )
                    convert_to_16k_mono_wav(mp3_path, wav_path, verbose=False)
                    db.add_content_file(db_conn, wav_path, content.id, "16k_mono_wav")

                transcription_queue.put((i, content, wav_path))
            except Exception as e:
                print(
                    f"{log_prefix(i)} Failed to prepare the wav of '{content.url}':\n{e}"
                )
                failed_preparations.append((content.url, e))
finally:
    preparation_done.set()

for consumer in consumers:
    consumer.join()

# Items handed back by a worker whose Whisper server became unreachable, with no worker left to take them
not_transcribed = []
while True:
    try:
        not_transcribed.append(transcription_queue.get_nowait()[2])
    except Empty:
        break

if len(failed_preparations) > 0:
    print("Failed wav preparations:")
    for content_url, exception in failed_preparations:
        print(f"  {content_url} : {exception}")
if len(failed_transcriptions) > 0:
    print("Failed transcriptions:")
    for wav_path, exception in failed_transcriptions:
        print(f"  {wav_path} : {exception}")
if len(not_transcribed) > 0:
    print("Not transcribed, a Whisper server became unreachable (re-run to retry):")
    for wav_path in not_transcribed:
        print(f"  {wav_path}")
if failed_preparations or failed_transcriptions or not_transcribed:
    sys.exit(1)
