"""Run processing steps (transcription, diarization, ...) on contents, spread over model servers.

    contents = [...]  # db.Content rows, e.g. from helpers.yt_dlp.list_channel_contents()
    report = run_steps(contents, [transcription_step(servers.WHISPER_SERVERS, "fr"),
                                  diarization_step(servers.PYANNOTE_SERVERS)])
    sys.exit(report.exit_code())

The main thread prepares the 16 kHz mono wav of each content (downloading its audio if needed),
while one worker thread per server runs a step on the wavs already prepared. Every step is
idempotent: what is already in the database is skipped, so re-running a script resumes it.
"""

import requests
import sqlite3
import threading
from dataclasses import dataclass, field
from math import log10
from pathlib import Path
from queue import Empty, Queue
from typing import Callable

import helpers.database as db
from helpers.ffmpeg import convert_to_16k_mono_wav


_print_lock = threading.Lock()


def log(message: str) -> None:
    """print() for the threads: their lines never mix."""
    with _print_lock:
        print(message, flush=True)


@dataclass
class Step:
    """A processing step. `run` returns a short description of what it stored."""

    name: str  # e.g. "transcription": used in messages ("Failed transcriptions:")
    verb: str  # e.g. "Transcribing"
    servers: list[str]
    is_done: Callable[[sqlite3.Connection, db.Content], bool]
    run: Callable[[sqlite3.Connection, str, db.Content, Path], str]


@dataclass
class Report:
    contents: int = 0
    already_done: int = 0
    failed_preparations: list = field(default_factory=list)  # (content, exception)
    done: dict = field(default_factory=dict)  # step name -> [content]
    failed: dict = field(default_factory=dict)  # step name -> [(content, exception)]
    not_run: dict = field(default_factory=dict)  # step name -> [content]: no server left

    def failures(self) -> int:
        return (
            len(self.failed_preparations)
            + sum(map(len, self.failed.values()))
            + sum(map(len, self.not_run.values()))
        )

    def exit_code(self) -> int:
        return 1 if self.failures() else 0


def transcription_step(whisper_servers: list[str], language: str = "fr") -> Step:
    from helpers.whisper_client import assert_whisper_servers, wav_to_whisper_result

    assert_whisper_servers(whisper_servers)

    def is_done(db_conn, content):
        return db.get_existing_content_file(db_conn, content.id, "whisper_result") is not None

    def run(db_conn, server, content, wav_path):
        result_path = Path(f"cache/whisper_result/{content.platform}_{content.external_id}.json")
        wav_to_whisper_result(server, wav_path, result_path, language)
        db.add_content_file(db_conn, result_path, content.id, "whisper_result")
        return str(result_path)

    return Step("transcription", "Transcribing", whisper_servers, is_done, run)


def diarization_step(pyannote_servers: list[str], params: dict | None = None) -> Step:
    from helpers.pyannote_client import assert_pyannote_servers, diarize_content

    model = assert_pyannote_servers(pyannote_servers)

    def is_done(db_conn, content):
        return db.get_diarization_id(db_conn, content.id, model, params) is not None

    def run(db_conn, server, content, wav_path):
        return diarize_content(db_conn, server, content, wav_path, model, params)

    return Step("diarization", "Diarizing", pyannote_servers, is_done, run)


def prepare_wav(db_conn: sqlite3.Connection, content: db.Content) -> Path:
    """The 16 kHz mono wav of a content, rebuilt (re-downloading its audio if needed) when it is
    not registered or no longer on disk. The mp3 is only needed to build the wav."""
    from helpers.yt_dlp import get_ytdlp_content_mp3

    wav_path = db.get_existing_content_file(db_conn, content.id, "16k_mono_wav")
    if wav_path is None:
        mp3_path = get_ytdlp_content_mp3(db_conn, content.url, content)
        wav_path = Path(f"cache/16k_mono_wav/{content.platform}_{content.external_id}.wav")
        convert_to_16k_mono_wav(mp3_path, wav_path, verbose=False)
        db.add_content_file(db_conn, wav_path, content.id, "16k_mono_wav")
    return wav_path


def run_steps(contents: list[db.Content], steps: list[Step]) -> Report:
    """Prepare the wav of every content that still needs a step, and run the steps on them.
    With no step, only the wavs are prepared (audio download + conversion)."""
    report = Report(contents=len(contents))
    for step in steps:
        report.done[step.name], report.failed[step.name], report.not_run[step.name] = [], [], []
    queues = {step.name: Queue() for step in steps}
    preparation_done = threading.Event()

    def log_prefix(i):
        n = max(len(contents), 1)
        return f"({i:{1 + int(log10(n))}}/{n})"

    def worker(step, server):
        queue = queues[step.name]
        with db.connect() as db_conn:
            while True:
                try:
                    item = queue.get(timeout=1)
                except Empty:
                    # Stop once preparations are over and no other worker can still hand an item back
                    if preparation_done.is_set() and queue.unfinished_tasks == 0:
                        return
                    continue
                i, content, wav_path = item
                try:
                    log(f"{log_prefix(i)} [{server}] {step.verb} {wav_path}...")
                    result = step.run(db_conn, server, content, wav_path)
                    report.done[step.name].append(content)
                    log(f"{log_prefix(i)} Done! -> {result}")
                except requests.exceptions.ConnectionError as e:
                    # This server is down: hand the item back to the other servers of the step and stop
                    queue.put(item)
                    log(f"{log_prefix(i)} Server {server} is unreachable, stopping its worker:\n{e}")
                    return
                except Exception as e:
                    report.failed[step.name].append((content, e))
                    log(f"{log_prefix(i)} {step.name.capitalize()} of '{wav_path}' failed:\n{e}")
                finally:
                    # After the hand-back put(): the count never drops to 0 while the item is pending
                    queue.task_done()

    # Daemon threads: Ctrl+C stops the script at once instead of waiting for the work in progress
    workers = [
        threading.Thread(target=worker, args=(step, server), daemon=True)
        for step in steps
        for server in step.servers
    ]
    for w in workers:
        w.start()

    try:
        with db.connect() as db_conn:
            for i, content in enumerate(contents, 1):
                try:
                    pending = [step for step in steps if not step.is_done(db_conn, content)]
                    if steps and not pending:
                        log(f"{log_prefix(i)} Already done: {content.url}")
                        report.already_done += 1
                        continue
                    wav_path = prepare_wav(db_conn, content)
                    if not steps:
                        log(f"{log_prefix(i)} Audio ready: {wav_path}")
                    for step in pending:
                        queues[step.name].put((i, content, wav_path))
                except Exception as e:
                    log(f"{log_prefix(i)} Failed to prepare the wav of '{content.url}':\n{e}")
                    report.failed_preparations.append((content, e))
    finally:
        preparation_done.set()

    for w in workers:
        w.join()

    # Items handed back by a worker whose server became unreachable, with no worker left to take them
    for step in steps:
        while True:
            try:
                report.not_run[step.name].append(queues[step.name].get_nowait()[1])
            except Empty:
                break

    print_report(report)
    return report


def print_report(report: Report) -> None:
    if report.failed_preparations:
        print("Failed wav preparations:")
        for content, e in report.failed_preparations:
            print(f"  {content.url} : {e}")
    for name, failed in report.failed.items():
        if failed:
            print(f"Failed {name}s:")
            for content, e in failed:
                print(f"  {content.url} : {e}")
    for name, not_run in report.not_run.items():
        if not_run:
            print(f"No {name}, every server of the step became unreachable (re-run to retry):")
            for content in not_run:
                print(f"  {content.url}")
    done = ", ".join(f"{len(contents)} {name}s" for name, contents in report.done.items())
    print(
        f"Summary: {report.contents} contents, {report.already_done} already done"
        + (f", done now: {done}" if done else "")
        + f", {report.failures()} failures."
    )
