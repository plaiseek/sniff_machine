"""Run processing steps (transcription, diarization, ...) on contents, spread over model servers.

    contents = [...]  # db.Content rows, e.g. from helpers.yt_dlp.list_contents()
    report = run_steps(contents, [transcription_step(servers.WHISPER_SERVERS, "fr"),
                                  diarization_step(servers.PYANNOTE_SERVERS)])
    sys.exit(report.exit_code())

The main thread prepares the 16 kHz mono wav of each content (downloading its audio if needed),
while one worker thread per server runs a step on the wavs already prepared (a step that needs no
audio, such as the sentence split, gets none). Every step is idempotent: what is already in the
database is skipped, so re-running a script resumes it.

A content is tried at most twice per step: an error hands it to another server of the step, a
dropped connection (the server crashed, e.g. out of memory on a long audio) hands it back while
the worker waits for its server to come back. A server not coming back, or stuck (no answer
within the upload timeout, see helpers/uploads.py), loses its worker. A server failing several
contents in a row only takes the contents no better server can take, until it succeeds again
(its error on a content it gets now and then, to see whether it works again, does not count).
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
import helpers.uploads as uploads
from helpers.ffmpeg import convert_to_16k_mono_wav


# How long a worker waits for its server to answer /health again after a dropped connection
SERVER_RETURN_TIMEOUT_S = 300
# A server failing that many contents in a row (errors or dropped connections) comes last when
# contents are handed out, except for one first try every RETRY_FAILING_SERVER_S: a success brings
# it back
MAX_CONSECUTIVE_FAILURES = 3
RETRY_FAILING_SERVER_S = 300

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
    run: Callable[[sqlite3.Connection, str, db.Content, Path | None], str]
    needs_audio: bool = True  # False: run gets no wav (None), and none is prepared for this step
    # Worker threads per server: a server that batches requests (vLLM) takes several contents at once.
    # If it drops their connections (it crashed), the other workers of the server take them back at
    # once and they fail a second time: re-run to retry them
    workers: int = 1


@dataclass
class Report:
    contents: int = 0
    already_done: int = 0
    failed_preparations: list = field(default_factory=list)  # (content, exception)
    done: dict = field(default_factory=dict)  # step name -> [content]
    failed: dict = field(default_factory=dict)  # step name -> [(content, exception)]
    not_run: dict = field(default_factory=dict)  # step name -> [content]: no server left
    stuck: list = field(default_factory=list)  # servers that did not answer within the upload timeout

    def failures(self) -> int:
        return (
            len(self.failed_preparations)
            + sum(map(len, self.failed.values()))
            + sum(map(len, self.not_run.values()))
        )

    def exit_code(self) -> int:
        """5 a server is probably stuck, 1 some contents failed, 0 everything done."""
        return 5 if self.stuck else 1 if self.failures() else 0


def transcription_step(whisper_servers: list[str], language: str = "fr") -> Step:
    from helpers.whisper_client import assert_whisper_servers, wav_to_whisper_result

    assert_whisper_servers(whisper_servers)

    def is_done(db_conn, content):
        return db.get_existing_content_file(db_conn, content.id, "whisper_result") is not None

    def run(db_conn, server, content, wav_path):
        result_path = Path(f"cache/whisper_result/{content.platform}_{content.external_id}.json")
        wav_to_whisper_result(server, wav_path, result_path, language)
        db.add_content_file(db_conn, result_path, content.id, "whisper_result")
        # Words stored from a previous result are stale: helpers.transcripts.update_transcripts() stores the new ones
        db.delete_transcription(db_conn, content.id)
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


def embedding_step(speechbrain_servers: list[str]) -> Step:
    """Voice embeddings of the exclusive turns of every diarization of a content. The steps of one
    run_steps() call run side by side: run this one in a later call than the diarization (a content
    not diarized yet has nothing to embed, so it counts as done)."""
    from helpers.speechbrain_client import assert_speechbrain_servers, embed_content_turns

    model = assert_speechbrain_servers(speechbrain_servers)

    def is_done(db_conn, content):
        return not db.get_unembedded_turns(db_conn, content.id, model)

    def run(db_conn, server, content, wav_path):
        return embed_content_turns(db_conn, server, content, wav_path, model)

    return Step("embedding", "Embedding the turns of", speechbrain_servers, is_done, run)


def sentence_step(wtpsplit_servers: list[str]) -> Step:
    """The sentences of the stored words of a content (see helpers/transcripts.py), never across
    two speakers. A content whose words are not stored (helpers.transcripts.update_transcripts())
    has nothing to split, so it counts as done. Needs no audio."""
    from helpers.transcripts import split_sentences
    from helpers.wtpsplit_client import assert_wtpsplit_servers

    model = assert_wtpsplit_servers(wtpsplit_servers)

    def is_done(db_conn, content):
        return db.sentences_done(db_conn, content.id, model)

    def run(db_conn, server, content, wav_path):
        return split_sentences(db_conn, content.id, server, model)

    return Step("sentence split", "Splitting into sentences", wtpsplit_servers, is_done, run, needs_audio=False)


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
    # One worker per address: a server listed twice would get a twin that takes the content it just
    # dropped while it restarts
    addresses = [list(dict.fromkeys(step.servers)) for step in steps]
    for name, step, servers in zip(names, steps, addresses, strict=True):
        if len(servers) < len(step.servers):
            log(f"Warning: a server is listed more than once for the {name} step, it gets one worker.")
    # Per step: the items waiting for a worker, as (i, content, wav path, servers that failed it),
    # the items being worked on, the servers whose workers still run (once per worker), and their
    # failures in a row
    pending = [[] for _ in steps]
    busy = [0 for _ in steps]
    alive = [[s for s in servers for _ in range(step.workers)] for step, servers in zip(steps, addresses)]
    errors = [dict.fromkeys(servers, 0) for servers in addresses]
    last_error = [dict.fromkeys(servers, 0.0) for servers in addresses]  # time.monotonic()
    tries = {}  # (step index, content id) -> [(server, error, how)]: its failed tries, see failure()
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

    def others(k, server):
        """Whether the worker of another server still runs."""
        return any(s != server for s in alive[k])

    def rank(k, server, tried):
        """How well a server fits an item that failed on `tried` (lower is better): the servers
        failing contents in a row come last (except for a first try now and then, never a last
        one), then those that failed this item, then (for a second try) those whose last content
        failed."""
        failing = errors[k][server]
        on_hold = failing >= MAX_CONSECUTIVE_FAILURES and (
            bool(tried) or time.monotonic() - last_error[k][server] < RETRY_FAILING_SERVER_S
        )
        return (on_hold, server in tried, bool(tried) and failing > 0)

    def take(k, server):
        """(the next item for the worker of this server, whether it is a probe), or None when no
        work is left. A worker only takes an item no running server fits better, so every item fits
        one of them. A probe: a server failing contents in a row gets an item while another server
        works, to see whether it works again."""
        with cond:
            while True:
                for n, item in enumerate(pending[k]):
                    tried = item[3]
                    if rank(k, server, tried) <= min(rank(k, s, tried) for s in alive[k]):
                        busy[k] += 1
                        probe = errors[k][server] >= MAX_CONSECUTIVE_FAILURES and any(
                            errors[k][s] < MAX_CONSECUTIVE_FAILURES for s in alive[k] if s != server
                        )
                        return pending[k].pop(n), probe
                if no_work_left(k):
                    return None
                cond.wait(timeout=1)

    def finish(k):
        with cond:
            busy[k] -= 1
            cond.notify_all()

    def succeeded(k, server):
        with cond:
            errors[k][server] = 0
            cond.notify_all()

    def failure(failed_tries):
        """The error to report for a content, from its failed tries: [(server, error, how)], `how`
        being "error", "dropped" (the connection), "stuck" (no answer within the timeout) or "probe"
        (an error that did not count, see retry_or_fail)."""
        server, error, how = failed_tries[-1]
        if len(failed_tries) == 1:
            return error
        first_server, _, first_how = failed_tries[-2]
        if how == first_how == "dropped" and first_server == server:
            return RuntimeError(f"{server} dropped the connection twice while working on it: {error}")
        last = {
            "error": str(error),
            "dropped": f"{server} dropped the connection while working on it: {error}",
            "stuck": f"{server} did not answer in time (stuck?): {error}",
        }[how]
        first = {
            "error": "it failed too, see the log",
            "probe": "it failed too, see the log",
            "dropped": "it dropped the connection",
            "stuck": "it did not answer in time",
        }[first_how]
        which = "first" if len(failed_tries) == 2 else "previous"
        return RuntimeError(f"{last} ({which} try, on {first_server}: {first})")

    def retry_or_fail(k, item, server, error, how, probe=False):
        """Hand the item back for another try (on another server, or on this one once it is back),
        or record its failure if it already failed once, or if no other server can take it."""
        i, content, wav_path, tried = item
        with cond:
            failed_tries = tries.setdefault((k, content.id), [])
            # The error of a probe does not count for the content (once per content)
            if how == "error" and probe and all(h != "probe" for _, _, h in failed_tries):
                how = "probe"
            failed_tries.append((server, error, how))
            errors[k][server] += 1
            last_error[k][server] = time.monotonic()
            counted = sum(h != "probe" for _, _, h in failed_tries)
            retry = counted < 2 and (how != "error" or others(k, server))
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
                while (taken := take(k, server)) is not None:
                    item, probe = taken
                    i, content, wav_path, _ = item
                    subject = wav_path or content.url
                    how = None
                    started = time.monotonic()
                    try:
                        log(f"{log_prefix(i)} [{server}] {step.verb} {subject}...")
                        result = step.run(db_conn, server, content, wav_path)
                        report.done[names[k]].append(content)
                        succeeded(k, server)
                        log(f"{log_prefix(i)} Done! -> {result}")
                    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                        # The server is down, crashed (maybe because of this content), gone (TCP
                        # keepalive gave up, which also raises a Timeout), or stuck: it did not
                        # answer within the upload timeout, which a server that works never reaches
                        elapsed = time.monotonic() - started
                        stuck = elapsed >= uploads.TIMEOUT_BASE_S
                        how = "stuck" if stuck else "dropped"
                        retried = retry_or_fail(k, item, server, e, how)
                        back = ", the content goes back to the queue" if retried else ", the content failed (second time)"
                        if stuck:
                            report.stuck.append(server)
                            log(
                                f"{log_prefix(i)} Server {server} did not answer within {elapsed / 3600:.1f} h"
                                + f"{back}. It is probably stuck: its worker stops (restart the server).\n{e}"
                            )
                        else:
                            log(
                                f"{log_prefix(i)} Server {server} is unreachable{back}: waiting up to"
                                + f" {SERVER_RETURN_TIMEOUT_S} s for the server to come back.\n{e}"
                            )
                    except Exception as e:
                        retried = retry_or_fail(k, item, server, e, "error", probe)
                        log(
                            f"{log_prefix(i)} {step.name.capitalize()} of '{subject}' failed on {server}"
                            + (" (another server will try)" if retried else "")
                            + f":\n{e}"
                        )
                    finally:
                        # After any hand-back, and before waiting: the wait ends once no work is left
                        finish(k)
                    if how == "stuck":
                        return
                    if how == "dropped":
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
        for k, servers in enumerate(addresses)
        for server in servers
        for _ in range(steps[k].workers)
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
                    # Only the steps that need it get the audio: the others never download it
                    needs_audio = not steps or any(steps[k].needs_audio for k in pending_steps)
                    wav_path = prepare_wav(db_conn, content) if needs_audio else None
                    if not pending_steps:
                        log(f"{log_prefix(i)} Audio ready: {wav_path}")
                    with cond:
                        for k in pending_steps:
                            pending[k].append((i, content, wav_path if steps[k].needs_audio else None, frozenset()))
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
    print_stuck(report)


def print_stuck(report: Report) -> None:
    for server in report.stuck:
        print(
            f"Server {server} did not answer within {uploads.TIMEOUT_BASE_S // 3600} h: it is probably stuck"
            " and needs a restart. Do not run this again before."
        )
