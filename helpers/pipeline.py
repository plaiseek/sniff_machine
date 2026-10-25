"""Run processing steps (transcription, diarization, ...) on contents, spread over model servers.

    contents = [...]  # db.Content rows, e.g. from helpers.yt_dlp.list_contents()
    report = run_steps(contents, [transcription_step(servers.WHISPER_SERVERS, "fr"),
                                  diarization_step(servers.PYANNOTE_SERVERS)])
    sys.exit(report.exit_code())

The main thread prepares the 16 kHz mono wav of each content (downloading its audio if needed),
while one worker thread per server runs a step on the wavs already prepared. Every step is
idempotent: what is already in the database is skipped, so re-running a script resumes it.

A content is tried at most twice per step: an error hands it to another server of the step, a
dropped connection (the server crashed, e.g. out of memory on a long audio) hands it back while
the worker waits for its server to come back. A server not coming back, or failing several
contents that another server then did, loses its worker.
"""

import requests
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from math import log10
from pathlib import Path
from typing import Callable

import helpers.database as db
from helpers.ffmpeg import convert_to_16k_mono_wav


# How long a worker waits for its server to answer /health again after a dropped connection
SERVER_RETURN_TIMEOUT_S = 300
# A server failing that many contents that another server then did (with no success of its own in
# between) is considered broken: its worker stops. Contents that fail everywhere never count.
MAX_CONSECUTIVE_FAILURES = 3

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
    With no step, only the wavs are prepared (audio download + conversion). A content whose steps
    are all done is skipped even if its audio was deleted since (e.g. to free disk space)."""
    report = Report(contents=len(contents))
    # Steps may share a name (e.g. two diarizations with different params): the report tells them apart
    names = []
    for step in steps:
        names.append(step.name if step.name not in names else f"{step.name} #{len(names) + 1}")
    for name in names:
        report.done[name], report.failed[name], report.not_run[name] = [], [], []
    # Per step: the items waiting for a worker, as (i, content, wav path, servers that failed it),
    # the items being worked on, and the servers whose worker still runs
    pending = [[] for _ in steps]
    busy = [0 for _ in steps]
    alive = [list(step.servers) for step in steps]
    strikes = [dict.fromkeys(step.servers, 0) for step in steps]  # see MAX_CONSECUTIVE_FAILURES
    tries = {}  # (step index, content id) -> [(server, error, dropped)]: its failed tries
    cond = threading.Condition()  # guards all of the above
    preparation_done = threading.Event()

    def log_prefix(i):
        n = max(len(contents), 1)
        return f"({i:{1 + int(log10(n))}}/{n})"

    def no_work_left(k):
        with cond:
            return preparation_done.is_set() and not pending[k] and busy[k] == 0

    def wait_for_server(k, server):
        """True once the server answers /health again, False if it does not within the timeout
        (or if no work is left for it)."""
        deadline = time.monotonic() + SERVER_RETURN_TIMEOUT_S
        while time.monotonic() < deadline and not no_work_left(k):
            try:
                if requests.get(f"http://{server}/health", timeout=5).status_code == 200:
                    return True
            except requests.exceptions.ReadTimeout:
                return True  # connected, but busy (e.g. a one-thread server diarizing): it is alive
            except requests.exceptions.RequestException:
                pass
            time.sleep(5)
        return False

    def others(k, server, tried=()):
        """Whether the worker of another server, that has not failed the item, still runs."""
        return any(s != server and s not in tried for s in alive[k])

    def take(k, server):
        """The next item for the worker of this server, or None when it must stop."""
        with cond:
            while True:
                if strikes[k][server] >= MAX_CONSECUTIVE_FAILURES and others(k, server):
                    log(f"Server {server} failed {strikes[k][server]} contents that another server then did: its worker stops.")
                    return None
                for n, item in enumerate(pending[k]):
                    tried = item[3]
                    # An item that failed on this server is left to another one, while there is one
                    if server not in tried or not any(s not in tried for s in alive[k]):
                        busy[k] += 1
                        return pending[k].pop(n)
                if no_work_left(k):
                    return None
                cond.wait(timeout=1)

    def finish(k):
        with cond:
            busy[k] -= 1
            cond.notify_all()

    def succeeded(k, server, content):
        with cond:
            strikes[k][server] = 0
            for s, _, dropped in tries.get((k, content.id), []):
                if s != server and not dropped:
                    strikes[k][s] += 1

    def failure(failed_tries):
        """The error to report for a content, from its failed tries."""
        _, error, dropped = failed_tries[-1]
        first = failed_tries[0] if len(failed_tries) == 2 else None
        if dropped:
            twice = " twice" if first and first[2] else ""
            error = RuntimeError(f"the server dropped the connection{twice} while working on it: {error}")
        if first and not (dropped and first[2]):
            how = "the server dropped the connection" if first[2] else "it failed too, see the log"
            error = RuntimeError(f"{error} (first try, on {first[0]}: {how})")
        return error

    def retry_or_fail(k, item, server, error, dropped):
        """Hand the item back for another try (on another server, or on this one once it is back),
        or record its failure if it already failed once, or if no other server can take it."""
        i, content, wav_path, tried = item
        with cond:
            failed_tries = tries.setdefault((k, content.id), [])
            failed_tries.append((server, error, dropped))
            retry = len(failed_tries) < 2 and (dropped or others(k, server))
            if retry:
                pending[k].append((i, content, wav_path, tried | {server}))
                cond.notify_all()
        if not retry:
            report.failed[names[k]].append((content, failure(failed_tries)))
        return retry

    def worker(k, server):
        step = steps[k]
        try:
            with db.connect() as db_conn:
                while (item := take(k, server)) is not None:
                    i, content, wav_path, _ = item
                    dropped = False
                    try:
                        log(f"{log_prefix(i)} [{server}] {step.verb} {wav_path}...")
                        result = step.run(db_conn, server, content, wav_path)
                        report.done[names[k]].append(content)
                        succeeded(k, server, content)
                        log(f"{log_prefix(i)} Done! -> {result}")
                    except requests.exceptions.ConnectionError as e:
                        # The server is down or crashed (maybe because of this content)
                        dropped = True
                        retried = retry_or_fail(k, item, server, e, dropped=True)
                        log(
                            f"{log_prefix(i)} Server {server} is unreachable"
                            + (", the content goes back to the queue" if retried else ", the content failed (second time)")
                            + f": waiting up to {SERVER_RETURN_TIMEOUT_S} s for the server to come back.\n{e}"
                        )
                    except Exception as e:
                        retried = retry_or_fail(k, item, server, e, dropped=False)
                        log(
                            f"{log_prefix(i)} {step.name.capitalize()} of '{wav_path}' failed on {server}"
                            + (" (another server will try)" if retried else "")
                            + f":\n{e}"
                        )
                    finally:
                        # After any hand-back, and before waiting: the wait ends once no work is left
                        finish(k)
                    if dropped:
                        if not wait_for_server(k, server):
                            if not no_work_left(k):
                                log(f"Server {server} did not come back: its worker stops.")
                            return
                        log(f"Server {server} is back.")
        finally:
            with cond:
                alive[k].remove(server)
                cond.notify_all()

    # Daemon threads: Ctrl+C stops the script at once instead of waiting for the work in progress
    workers = [
        threading.Thread(target=worker, args=(k, server), daemon=True)
        for k, step in enumerate(steps)
        for server in step.servers
    ]
    for w in workers:
        w.start()

    try:
        with db.connect() as db_conn:
            for i, content in enumerate(contents, 1):
                try:
                    pending_steps = [k for k, step in enumerate(steps) if not step.is_done(db_conn, content)]
                    audio_missing = not steps and (
                        db.get_existing_content_file(db_conn, content.id, "16k_mono_wav") is None
                    )
                    if not pending_steps and not audio_missing:
                        log(f"{log_prefix(i)} Already done: {content.url}")
                        report.already_done += 1
                        continue
                    wav_path = prepare_wav(db_conn, content)
                    if not pending_steps:
                        log(f"{log_prefix(i)} Audio ready: {wav_path}")
                    with cond:
                        for k in pending_steps:
                            pending[k].append((i, content, wav_path, frozenset()))
                        cond.notify_all()
                except Exception as e:
                    report.failed_preparations.append((content, e))
                    log(f"{log_prefix(i)} Failed to prepare the wav of '{content.url}':\n{e}")
    finally:
        with cond:
            preparation_done.set()
            cond.notify_all()

    for w in workers:
        w.join()

    # Items left when every worker of their step stopped (servers unreachable or broken)
    for k in range(len(steps)):
        report.not_run[names[k]] += [item[1] for item in sorted(pending[k], key=lambda item: item[0])]

    print_report(report)
    return report


def print_report(report: Report) -> None:
    if report.failed_preparations:
        print("Failed wav preparations:")
        for content, e in report.failed_preparations:
            print(f"  {content.url} : {e}")
    plural = lambda name: name if "#" in name else f"{name}s"  # noqa: E731
    for name, failed in report.failed.items():
        if failed:
            print(f"Failed {plural(name)}:")
            for content, e in failed:
                print(f"  {content.url} : {e}")
    for name, not_run in report.not_run.items():
        if not_run:
            print(f"No {name}, no server of the step was left (re-run to retry):")
            for content in not_run:
                print(f"  {content.url}")
    done = ", ".join(f"{len(contents)} {plural(name)}" for name, contents in report.done.items())
    print(
        f"Summary: {report.contents} contents, {report.already_done} already done"
        + (f", done now: {done}" if done else "")
        + f", {report.failures()} failures."
    )
