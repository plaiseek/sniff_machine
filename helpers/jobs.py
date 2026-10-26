"""Run a job: select contents (listings such as channels, and/or single URLs, optionally by upload
date), then download, transcribe and/or diarize them, and/or compute the voice embeddings of their
diarized turns, and/or split their transcriptions into sentences. A job that transcribes, diarizes
or splits also stores the words of the transcriptions in the database, with their speaker turns.
Job scripts (jobs/*.py, copied from jobs/job_template.py) only hold settings and call run_job().

Exit codes: 0 everything done, 1 some contents failed (re-run the job to retry them),
2 invalid settings, 3 the same job is already running, 4 a server needed by a step is down.
"""

import datetime
import sys
import traceback
from pathlib import Path

import helpers.database as db
import servers

STEPS = ("download", "transcribe", "diarize", "embed", "split")


def _check_settings(listing_urls, content_urls, uploaded_from, uploaded_to, steps, language, diarization_params) -> list[str]:
    errors = []
    for name, urls in (("LISTING_URLS", listing_urls), ("CONTENT_URLS", content_urls)):
        if not isinstance(urls, (list, tuple)) or not all(isinstance(u, str) and u.startswith("http") for u in urls):
            errors.append(f"{name} must be a list of http(s) URLs, got {urls!r}")
    if not errors and not listing_urls and not content_urls:
        errors.append("Nothing to select: give LISTING_URLS and/or CONTENT_URLS")
    for name, value in (("UPLOADED_FROM", uploaded_from), ("UPLOADED_TO", uploaded_to)):
        if value is not None:
            try:
                datetime.date.fromisoformat(value)
                if len(value) != 10:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"{name} must be None or a 'YYYY-MM-DD' date, got {value!r}")
    if not errors and uploaded_from and uploaded_to and uploaded_from > uploaded_to:
        errors.append(f"UPLOADED_FROM ({uploaded_from}) is after UPLOADED_TO ({uploaded_to})")
    if (uploaded_from or uploaded_to) and not listing_urls:
        errors.append("UPLOADED_FROM/UPLOADED_TO only filter LISTING_URLS: give a listing, or no dates")
    if not isinstance(steps, (list, tuple)) or not steps or any(s not in STEPS for s in steps):
        errors.append(f"STEPS must be a non-empty list of {list(STEPS)}, got {steps!r}")
    if not isinstance(language, str) or not (language == "auto" or (len(language) == 2 and language.isalpha())):
        errors.append(f"LANGUAGE must be a 2-letter code such as 'fr', or 'auto', got {language!r}")
    if not isinstance(diarization_params, dict):
        errors.append(f"DIARIZATION_PARAMS must be a dict, got {diarization_params!r}")
    else:
        for key, value in diarization_params.items():
            if key not in ("num_speakers", "min_speakers", "max_speakers") or type(value) is not int or not 1 <= value <= 50:
                errors.append(f"DIARIZATION_PARAMS: {key!r} = {value!r} is not allowed (num_speakers, min_speakers, max_speakers: integers from 1 to 50)")
        if any(e.startswith("DIARIZATION_PARAMS") for e in errors):
            pass  # the combinations below need valid values
        elif "num_speakers" in diarization_params and len(diarization_params) > 1:
            errors.append("DIARIZATION_PARAMS: give either num_speakers, or min_speakers and/or max_speakers")
        elif diarization_params.get("min_speakers", 1) > diarization_params.get("max_speakers", 50):
            errors.append("DIARIZATION_PARAMS: min_speakers is greater than max_speakers")
    return errors


def _lock(job_path: Path):
    """Exclusive lock held while the job runs, so that it never runs twice at once. It is taken on a
    file of its own (cache/locks/<job>.lock): an editor or `sed -i` may replace the job file itself."""
    try:
        import fcntl
    except ImportError:  # not on Linux/macOS: no lock
        return True
    lock_path = db.db_path.parent / "locks" / f"{job_path.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "a")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return None
    return lock_file  # kept open (and locked) until the process exits


def print_contents(db_conn, contents: list[db.Content], diarization_params: dict | None = None) -> None:
    """One line per content: what is already stored for it."""
    params = db.params_key(diarization_params)
    print(f"{'id':>6}  {'uploaded':10}  {'duration':>8}  {'audio':5}  {'transcript':10}  {'diarization':18}  {'voices':6}  {'sentences':9}  title")
    for c in contents:
        audio = "yes" if db.get_existing_content_file(db_conn, c.id, "16k_mono_wav") else "no"
        transcript = "yes" if db.get_existing_content_file(db_conn, c.id, "whisper_result") else "no"
        row = db_conn.execute(
            """SELECT COUNT(DISTINCT speaker), COUNT(*) FROM turns WHERE exclusive = 1 AND diarization_id =
(SELECT id FROM diarizations WHERE content_id = ? AND params = ? ORDER BY id DESC LIMIT 1)""",
            (c.id, params),
        ).fetchone()
        has_run = db_conn.execute(
            "SELECT 1 FROM diarizations WHERE content_id = ? AND params = ?", (c.id, params)
        ).fetchone()
        diarization = f"{row[0]} spk, {row[1]} turns" if has_run else "no"
        # Voice embeddings (of any model) of every exclusive turn of every diarization of the content
        unembedded = db_conn.execute(
            """SELECT COUNT(*) FROM turns t JOIN diarizations d ON d.id = t.diarization_id WHERE d.content_id = ?
AND t.exclusive = 1 AND NOT EXISTS (SELECT 1 FROM turn_embeddings e WHERE e.turn_id = t.id)""",
            (c.id,),
        ).fetchone()[0]
        any_run = db_conn.execute("SELECT 1 FROM diarizations WHERE content_id = ?", (c.id,)).fetchone()
        voices = "-" if not any_run else "no" if unembedded else "yes"
        # Split from the words as they are aligned now (any splitter); "-": no words (stored)
        split = db_conn.execute(
            """SELECT t.diarization_id IS (SELECT MAX(id) FROM diarizations WHERE content_id = t.content_id),
EXISTS (SELECT 1 FROM sentences s WHERE s.content_id = t.content_id),
EXISTS (SELECT 1 FROM words w WHERE w.content_id = t.content_id) FROM transcriptions t WHERE t.content_id = ?""",
            (c.id,),
        ).fetchone()
        sentences = "-" if split is None or not split[2] else "yes" if split[0] and split[1] else "no"
        duration = "?" if c.duration_s is None else str(datetime.timedelta(seconds=round(c.duration_s)))
        print(
            f"{c.id:>6}  {(c.upload_date or '?')[:10]:10}  {duration:>8}  {audio:5}  {transcript:10}  {diarization:18}  {voices:6}  {sentences:9}  {(c.title or '')[:60]}"
        )


EXIT_CODES = {
    0: "everything is done",
    1: "some contents failed: run the job again to retry them",
    2: "invalid settings, nothing done",
    3: "this job is already running",
    4: "a server is not ready, nothing done",
    5: "a server is probably stuck (see above): tell the user, it needs a restart, do not run the job again before",
}


def run_job(*args, **kwargs) -> int:
    """_run_job(), ending with a line that gives the exit code (the log of a job run in the
    background then says how it ended), even when it crashes or is interrupted.
    `python3.11 jobs/<job>.py --status` only says whether the job is running."""
    if "--status" in sys.argv[1:]:
        job_path = Path(args[0] if args else kwargs["job_path"]).resolve()
        print(f"{job_path.name}: " + ("NOT RUNNING" if _lock(job_path) else "RUNNING"))
        return 0
    dry_run = kwargs.get("dry_run", args[8] if len(args) > 8 else False)
    try:
        code = _run_job(*args, **kwargs)
    except KeyboardInterrupt:
        print("Job finished with exit code 130: interrupted, run the job again to resume.", flush=True)
        return 130
    except Exception as e:
        # The error first, so that the final line is always the last one of the log
        traceback.print_exc(file=sys.stdout)
        print(f"Job finished with exit code 1: crashed ({type(e).__name__}: {e}), see the error above.", flush=True)
        return 1
    if dry_run and code == 0:
        print("Job finished with exit code 0: dry run only, nothing downloaded or computed yet.", flush=True)
    else:
        print(f"Job finished with exit code {code}: {EXIT_CODES[code]}.", flush=True)
    return code


def _run_job(
    job_path: str,
    listing_urls: list[str],
    content_urls: list[str],
    uploaded_from: str | None,
    uploaded_to: str | None,
    steps: list[str],
    language: str = "fr",
    diarization_params: dict | None = None,
    dry_run: bool = False,
) -> int:
    from helpers.pipeline import diarization_step, embedding_step, print_stuck, run_steps, sentence_step, transcription_step
    from helpers.yt_dlp import get_ytdlp_content, list_contents

    diarization_params = {} if diarization_params is None else diarization_params
    errors = _check_settings(listing_urls, content_urls, uploaded_from, uploaded_to, steps, language, diarization_params)
    if errors:
        print("Invalid settings, nothing done:")
        for error in errors:
            print(f"  - {error}")
        return 2
    job_path = Path(job_path).resolve()
    lock = _lock(job_path)  # held as long as this variable lives, i.e. until the job returns
    if not lock:
        print(f"{job_path.name} is already running: wait for it to finish (its log ends with 'Job finished').")
        return 3

    # Fail fast, before any download, if a server needed by a step is down
    gpu_steps = []
    embedding = splitting = None
    if not dry_run:
        try:
            if "transcribe" in steps:
                gpu_steps.append(transcription_step(servers.WHISPER_SERVERS, language))
            if "diarize" in steps:
                gpu_steps.append(diarization_step(servers.PYANNOTE_SERVERS, diarization_params))
            if "embed" in steps:
                embedding = embedding_step(servers.SPEECHBRAIN_SERVERS)
            if "split" in steps:
                splitting = sentence_step(servers.WTPSPLIT_SERVERS)
        except SystemExit as e:  # the client's server check failed
            print(f"{e.code}\nNothing done: a server needed by the steps is not ready (see model_servers/check_servers.py).")
            return 4

    skipped = []  # (url, reason, is_error): listing entries or URLs left out
    contents = {}
    with db.connect() as db_conn:
        for url in listing_urls:
            print(f"Listing {url}...")
            try:
                selected, listing_skipped = list_contents(db_conn, url, uploaded_from, uploaded_to)
            except Exception as e:  # e.g. a wrong URL, a network error
                message = str(e).splitlines()[0] if str(e) else type(e).__name__
                print(f"  Cannot list {url}: {message}")
                skipped.append((url, f"cannot list it: {message}", True))
                continue
            print(f"  {len(selected)} contents selected")
            skipped += listing_skipped
            for content in selected:
                contents.setdefault(content.id, content)
        for url in content_urls:
            try:
                content = get_ytdlp_content(db_conn, url)
                contents.setdefault(content.id, content)
            except Exception as e:
                print(f"Cannot fetch {url}:\n{e}")
                skipped.append((url, str(e).splitlines()[0] if str(e) else type(e).__name__, True))
        # Oldest first: the sources that expire first (e.g. Twitch VODs) are processed first
        contents = sorted(contents.values(), key=lambda c: (c.upload_date is None, c.upload_date or "", c.id))

        if dry_run or not contents:
            print_contents(db_conn, contents, diarization_params)

    print(f"{len(contents)} contents selected" + (f", {len(skipped)} entries skipped:" if skipped else "."))
    for url, reason, is_error in skipped:
        print(f"  {url} : {reason}" + (" (error: run the job again to retry)" if is_error else ""))
    n_errors = sum(1 for _, _, is_error in skipped if is_error)
    if dry_run:
        print("Dry run: nothing downloaded or computed (set DRY_RUN = False to run the job).")
        return 1 if n_errors else 0

    # With GPU steps, the audio is only fetched for the contents that still need one: audio deleted
    # from cache/ to free disk space does not come back (STEPS = ["download"] alone fetches it)
    reports = []
    if gpu_steps or "download" in steps:
        reports.append(run_steps(contents, gpu_steps))
    word_failures = []
    if "transcribe" in steps or "diarize" in steps or "split" in steps:
        # The words of the transcriptions, each with its turn of the latest diarization
        from helpers.transcripts import update_transcripts

        with db.connect() as db_conn:
            word_failures = update_transcripts(db_conn, [c.id for c in contents])
    later_steps = [step for step in (embedding, splitting) if step is not None]
    if later_steps:
        # After the diarizations and the words, which they need. The embeddings leave out the
        # contents never diarized (run_steps would download their audio for nothing), the
        # sentences those without stored words (not transcribed)
        with db.connect() as db_conn:
            def having(table):
                return {c.id for c in contents if db_conn.execute(f"SELECT 1 FROM {table} WHERE content_id = ?", (c.id,)).fetchone()}

            diarized = having("diarizations") if embedding is not None else set()
            transcribed = having("transcriptions") if splitting is not None else set()
        if embedding is not None:
            print(f"Voice embeddings of the turns of {len(diarized)} diarized contents ({len(contents) - len(diarized)} not diarized, left out).")
        if splitting is not None:
            print(f"Sentences of {len(transcribed)} transcribed contents ({len(contents) - len(transcribed)} not transcribed, left out).")
        reports.append(run_steps([c for c in contents if c.id in diarized | transcribed], later_steps))
    with db.connect() as db_conn:
        print_contents(db_conn, contents, diarization_params)
    if skipped:
        print(f"{len(skipped)} listing entries or URLs were skipped ({n_errors} errors), see above.")
    for report in reports:
        print_stuck(report)  # again, next to the final line that the agent reads
    if any(report.stuck for report in reports):
        return 5
    return 1 if any(report.failures() for report in reports) or word_failures or n_errors else 0
