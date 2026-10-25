---
name: write-pipeline-script
description: Writes a new Python script for this repository when jobs/job_template.py cannot do what is asked - a custom selection of contents, a new processing step sent to a model server, or an export of results (CSV, text) from the database. Gives the building blocks (helpers.database, helpers.yt_dlp, helpers.pipeline, servers.py), the conventions, and how to test the script safely.
---

# Write a pipeline script

First check whether the `pipeline-job` skill covers the request: selecting listings, URLs and upload dates, then download / transcribe / diarize. If it does, use it instead.

## Where the script goes

- A one-off task: `jobs/<today>_<short_name>.py`, starting with the same 4 lines as `jobs/job_template.py` (after its settings) so that it runs from the repository root:
  ```python
  import os, sys
  from pathlib import Path
  ROOT = Path(__file__).resolve().parents[1]
  os.chdir(ROOT); sys.path.insert(0, str(ROOT))
  ```
- A new reusable feature: a script at the repository root, like `diarize_contents.py`. Put its settings in UPPERCASE constants at the top.

## Building blocks

```python
import helpers.database as db          # the database: always go through these functions
import servers                         # server address lists: servers.WHISPER_SERVERS, PYANNOTE_SERVERS, ...
from helpers.yt_dlp import list_contents, get_ytdlp_content
from helpers.pipeline import prepare_wav, run_steps, transcription_step, diarization_step, embedding_step, Step
```

| Function | What it does |
|---|---|
| `with db.connect() as db_conn:` | open (create or migrate) the database; the block commits at its end |
| `db.find_contents(db_conn, ids=None, platform=None, channel_id=None, uploader=None, title=None, uploaded_from=None, uploaded_to=None)` | contents already in the database matching all the filters (substring for `uploader`/`title`, inclusive `YYYY-MM-DD` dates), oldest first |
| `list_contents(db_conn, listing_url, uploaded_from=None, uploaded_to=None)` | list a channel / playlist and register its contents (network): `(contents, skipped)`, `skipped` = `[(url, reason, is_error)]` |
| `get_ytdlp_content(db_conn, url)` | the `db.Content` of one URL, registered if new (network the first time) |
| `db.get_existing_content_file(db_conn, content.id, file_type, variant="")` | `Path` of a registered file still on disk, else `None` |
| `prepare_wav(db_conn, content)` | the content's 16 kHz mono wav, downloading the audio if needed |
| `run_steps(contents, [transcription_step(servers.WHISPER_SERVERS, "fr"), diarization_step(servers.PYANNOTE_SERVERS, {"num_speakers": 2})])` | download, then transcribe / diarize on every server in parallel, skipping what is done; returns a report: `sys.exit(report.exit_code())` |
| `db.get_diarization_id(db_conn, content.id, model, params)`, `db.get_turns(db_conn, diarization_id, exclusive=True)` | a stored diarization and its turns (`speaker`, `start_ms`, `end_ms`, `overlap_ms`) |
| `run_steps(diarized_contents, [embedding_step(servers.SPEECHBRAIN_SERVERS)])` | voice embeddings of the exclusive turns, in a later `run_steps` call than the diarization (the steps of one call run side by side) |
| `db.get_speaker_labels(db_conn, diarization_id)` | `{label: (speaker_id, name, source, similarity)}`: who the labels are |
| `import helpers.speakers as sp`: `sp.label_voices(db_conn, diarization_id, model)`, `sp.speaker_voices(db_conn, model)`, `sp.best_match(voice, speakers)`, `sp.is_clean(turn)` | voice vectors of the labels and of the named speakers, the closest speaker; never write labels yourself, `identify_speakers.py` does |
| `from helpers.whisper_client import whisper_result_to_words` | the words of a `whisper_result` file as `(text, start_ms, end_ms)`, on the same timeline as the turns |
| `db.add_content_file(db_conn, path, content.id, file_type, variant="")`, `db.write_atomic(path, text)` | register a file you produced; write it without ever leaving a half-written file |

`db.Content` fields: `id, platform, external_id, url, title, duration_s, uploader, upload_date, channel_id, series`. The read-only SQL of the `query-database` skill shows the tables.

A new step sent to a model server is a `Step(name, verb, servers, is_done, run)`: `is_done(db_conn, content) -> bool` says whether its result is already stored; `run(db_conn, server, content, wav_path) -> str` computes it on one server, stores it, and returns a one-line summary. `run_steps` then handles the threads, the servers that go down and the failure report. Look at `transcription_step` in `helpers/pipeline.py` for a complete example.

## Example: speaking time per speaker of a channel's diarized videos, as CSV

Saved as `jobs/<today>_speaking_time.py` and run with `python3.11 jobs/<today>_speaking_time.py`:

```python
import csv, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT); sys.path.insert(0, str(ROOT))
import helpers.database as db

UPLOADER = "psyhodelik"          # substring of the channel name
OUTPUT = "jobs/speaking_time.csv"

with db.connect() as db_conn:
    contents = db.find_contents(db_conn, uploader=UPLOADER)
    with open(OUTPUT, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["content_id", "title", "speaker", "minutes"])
        for content in contents:
            row = db_conn.execute("SELECT MAX(id) FROM diarizations WHERE content_id = ?", (content.id,)).fetchone()
            if row[0] is None:
                continue  # not diarized
            totals = {}
            for turn in db.get_turns(db_conn, row[0], exclusive=True):
                totals[turn.speaker] = totals.get(turn.speaker, 0) + turn.end_ms - turn.start_ms
            for speaker, ms in sorted(totals.items()):
                writer.writerow([content.id, content.title, speaker, round(ms / 60000, 1)])
print(f"Wrote {OUTPUT} ({len(contents)} contents of '{UPLOADER}')")
```

## Conventions

- Every step is idempotent: check what is stored before computing, store (and register files) only after success. Re-running a script must resume, never redo or duplicate.
- Paths are relative to the repository root and live under `cache/<file_type>/<platform>_<external_id>.<ext>`.
- Write to the database only through `helpers.database` functions. **Never** `DELETE`, `UPDATE` or `DROP` by hand, and never change the schema: a new table needs a new entry appended to `MIGRATIONS` in `helpers/database.py`; ask the user before adding one.
- Never delete or move `cache/`, `cache/test.db` or `cache/backups/`.
- Long scripts print one line per content with a `(i/n)` prefix and end with a summary of failures, then exit with 1 if anything failed.

## Test before the real run

1. `python3.11 -m py_compile <script>.py` must print nothing.
2. Run it on one content first (e.g. `db.find_contents(db_conn, ids=[<one id>])`), check its output and the database with the `query-database` skill.
3. Then run it on everything, in the background with a log if it takes more than a few minutes: `nohup python3.11 <script>.py >> <script>.log 2>&1 &`.
