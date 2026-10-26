---
name: write-pipeline-script
description: Writes a new Python script for this repository when jobs/job_template.py cannot do what is asked - a custom selection of contents, a new processing step sent to a model server, or an export of results (CSV, text) from the database. Gives the building blocks (helpers.database, helpers.yt_dlp, helpers.pipeline, servers.py), the conventions, and how to test the script safely.
---

# Write a pipeline script

First check whether the `pipeline-job` skill covers the request: selecting listings, URLs and upload dates, then download / transcribe / diarize / embed (voice embeddings) / split into sentences. If it does, use it instead. An export of what an LLM question found is `explore/ask_transcripts.py --export` (see the `findings-dossier` skill), not a new script.

## Where the script goes

- A one-off task: `jobs/<today>_<short_name>.py` (`<today>` is `date +%F`; `<short_name>` uses only lowercase letters, digits and `_`), starting with the same root lines as `jobs/job_template.py` (after its settings) so that it runs from the repository root:
  ```python
  import os, sys
  from pathlib import Path
  ROOT = Path(__file__).resolve().parents[1]
  os.chdir(ROOT); sys.path.insert(0, str(ROOT))
  ```
- A new reusable feature: a script in the folder of its task (`pipeline/`, `speakers/`, `explore/`, ...), like `pipeline/split_sentences.py`: the same root lines, a docstring with example commands, its options with `argparse` (so that `--help` works).
- What a script writes for the user (CSV, text, clips) goes under `exports/<today>_<short_name>/`, which is git-ignored: never commit it, and never write it in `jobs/` or `cache/`.

## Building blocks

```python
import helpers.database as db          # the database: writes always go through these functions; reads may also use db_conn.execute("SELECT ...")
import servers                         # server address lists: servers.WHISPER_SERVERS, PYANNOTE_SERVERS, ...
from helpers.yt_dlp import list_contents, get_ytdlp_content
from helpers.pipeline import prepare_wav, run_steps, transcription_step, diarization_step, embedding_step, sentence_step, Step
```

| Function | What it does |
|---|---|
| `with db.connect() as db_conn:` | open the database (created if missing; an old schema is migrated first, after a backup in `cache/backups/`); the block commits at its end |
| `db.find_contents(db_conn, ids=None, platform=None, channel_id=None, uploader=None, title=None, uploaded_from=None, uploaded_to=None)` | contents already in the database matching all the filters (substring for `uploader`/`title`, inclusive `YYYY-MM-DD` dates), oldest first |
| `list_contents(db_conn, listing_url, uploaded_from=None, uploaded_to=None)` | list a channel / playlist and register its contents (network): `(contents, skipped)`, `skipped` = `[(url, reason, is_error)]` |
| `get_ytdlp_content(db_conn, url)` | the `db.Content` of one URL, registered if new (network the first time) |
| `db.get_existing_content_file(db_conn, content.id, file_type, variant="")` | `Path` of a registered file still on disk, else `None` |
| `prepare_wav(db_conn, content)` | the content's 16 kHz mono wav, downloading the audio if needed |
| `run_steps(contents, [transcription_step(servers.WHISPER_SERVERS, "fr"), diarization_step(servers.PYANNOTE_SERVERS, {"num_speakers": 2})])` | download, then transcribe / diarize on every server kept (see below) in parallel, skipping what is done; returns a report. Then store the words: see the code below the table |
| `db.get_diarization_id(db_conn, content.id, model, params)`, `db.get_turns(db_conn, diarization_id, exclusive=True)` | a stored diarization and its turns (`speaker`, `start_ms`, `end_ms`, `overlap_ms`); `model` is the `diarizations.model` column (or the first value `assert_pyannote_servers()` returns), `params` a dict (`{}` by default) |
| `run_steps(diarized_contents, [embedding_step(servers.SPEECHBRAIN_SERVERS)])` | voice embeddings of the exclusive turns, in a later `run_steps` call than the diarization (the steps of one call run side by side) |
| `db.get_speaker_labels(db_conn, diarization_id)` | `{label: (speaker_id, name, source, similarity)}`: who the labels are |
| `from helpers.speakers import describe_label`: `describe_label(labels, label)` | who a label is, from `db.get_speaker_labels()`: `"Jean Dupont (human)"`, `"Jean Dupont (auto, 0.92)"`, `"unknown (auto, 0.41)"` (compared, nobody close enough), `"nobody known (human)"`, or `""` when never identified. Print names this way: an automatic name is a guess with its similarity |
| `import helpers.speakers as sp`: `sp.label_voices(db_conn, diarization_id, model)`, `sp.speaker_voices(db_conn, model)`, `sp.best_match(voice, speakers)`, `sp.is_clean(turn)` | voice vectors of the labels and of the named speakers, the closest speaker; never write labels yourself, `speakers/identify_speakers.py` does |
| `db.get_words(db_conn, content.id)`, `db.get_words_with_speakers(db_conn, content.id)` | the stored words of a content in order (`idx`, `text`, `norm`, `start_ms`, `end_ms`, `prob`, `turn_id`), the second with the label of their turn (None: no turn) |
| `db.get_transcription_diarization(db_conn, content.id)` | the id of the diarization the stored words are aligned to (None: none, or words not stored): the `speaker` labels of the words and sentences are its labels |
| `db.get_sentences(db_conn, content.id)` | its stored sentences: `(first_idx, last_idx, start_ms, end_ms, speaker, text)`; `[]` for a content not split (see `pipeline/split_sentences.py`). `helpers.transcripts.content_sentences(db_conn, content.id)` gives the same tuples, its words cut on punctuation when none are stored |
| `from helpers.transcripts import update_transcripts` | `update_transcripts(db_conn, [content ids])` stores the words of the transcriptions not stored yet and aligns them to new diarizations (prints a line per content); returns the failures |
| `run_steps(transcribed_contents, [sentence_step(servers.WTPSPLIT_SERVERS)])` | the sentences of the stored words (wtpsplit), never across two speakers; needs no audio. `helpers.transcripts.split_sentences(db_conn, content.id)` splits on punctuation without a server and stores the result in place of the content's sentences, wtpsplit ones included: call it only on contents with no sentences yet (`not db.sentences_done(db_conn, content.id, None)`); it returns a one-line summary. To read sentences without storing anything, use `content_sentences` |
| `from helpers.transcripts import speaker_runs, content_link, hms` | the words in runs of one speaker; `content_link(content, ms)`: the URL at that moment (YouTube, Twitch; other platforms: `<url> (at H:MM:SS)`, not a link); `hms(ms)`: `H:MM:SS` |
| `from helpers.vllm_client import assert_vllm_servers, chat_json` | `vllm = assert_vllm_servers(servers.VLLM_SERVERS)`: the vLLM servers that answer, their model, whether it thinks first (`vllm.reasoning`); `chat_json(address, vllm.model, system, user, schema, thinking, max_tokens, sampling, timeout_s=3600)` -> `Reply(answer, prompt_tokens, completion_tokens, finish_reason)`, `answer` a dict, None when there is none (`finish_reason` `"length"`: cut by `max_tokens`; otherwise the model ended while thinking: ask again) |
| `from helpers.questions import question_step, question_text, settings` | the two-pass question of `explore/ask_transcripts.py` as a step: `db.add_question(db_conn, question_text(question, criteria), speaker, vllm.model, settings(vllm.reasoning))`, then `run_steps(contents, [question_step(vllm, ServerHints(clients, kv_tokens, tokens_per_s), question_id, question, criteria, speaker, words)])` (`ServerHints` from `helpers.vllm_client`: what each server can do; `words`: of the contents not done, for the time left); `db.get_findings(db_conn, question_id)` reads what it found |
| `from helpers.whisper_client import whisper_result_to_words` | the words of a `whisper_result` file (`text`, `start_ms`, `end_ms`, `prob`), on the same timeline as the turns: the stored words come from it |
| `db.add_content_file(db_conn, path, content.id, file_type, variant="")`, `db.write_atomic(path, text)` | register a file you produced; write a text file (`path` a `Path`; UTF-8, its folder created) without ever leaving a half-written file |

After transcription or diarization steps, store the words before anything else reads them (a later `sentence_step` counts a content whose words are not stored as done), and fold their failures into the exit code, as `pipeline/diarize_contents.py` does:

```python
report = run_steps(contents, [step])
with db.connect() as db_conn:
    failures = update_transcripts(db_conn, [content.id for content in contents])
sys.exit(report.exit_code() or (1 if failures else 0))
```

`report.exit_code()` is 0 (everything done), 1 (some contents failed: re-run to retry them) or 5 (a server is probably stuck: tell the user, do not re-run).

Building a step (`transcription_step(...)`, ...) checks its servers and keeps only those that answer: always build it from the `servers.X_SERVERS` list, never pass addresses to `Step()` yourself. A listed server that does not answer is left out (`Warning: <Kind> server <address> is not ready, it is left out: <reason>`, then `<Kind>: N of M servers used: <addresses>`); a busy or loading one is kept, and work sent to it waits. It exits (`SystemExit`, exit code 1 in a script) when no listed server answers (`No <Kind> server of [...] is ready`), when none is listed, when every server kept stays busy for 600 s (`No <Kind> server answered /health within 600 s`: they work for another job, run it again later) or when they run different models: build the steps before any long work, as `pipeline/split_sentences.py` does (`helpers/jobs.py` catches it and exits with 4). The `assert_*_servers()` functions behind the steps return `(model, ready addresses)` (Whisper: the addresses only).

`db.Content` fields: `id, platform, external_id, url, title, duration_s, uploader, upload_date, channel_id, series`. The read-only SQL of the `query-database` skill shows the tables.

A new step sent to a model server is a `Step(name, verb, servers, is_done, run)`: `is_done(db_conn, content) -> bool` says whether its result is already stored; `run(db_conn, server, content, wav_path) -> str` computes it on one server, stores it, and returns a one-line summary. A step that needs no audio passes `needs_audio=False` (its `run` gets `wav_path=None`, and no audio is downloaded for it). A server that batches requests (vLLM) gets several worker threads with `workers=N` (`question_step` also sends a content's requests at once, through `helpers.vllm_client.Slots`). `run_steps` then handles the threads, the servers that go down during the run and the failure report. `is_done` and `run` run in other threads: use only the `db_conn` they are given, never a connection opened by the script. Look at `transcription_step` in `helpers/pipeline.py` for a complete example.

## Subtitles

Subtitles are not part of the workflow: the Whisper large-v3 transcriptions are far better than automatic subtitles, and mixing subtitles into them would bias what is counted and searched. Only when a request asks for subtitles that people wrote (e.g. to compare with the transcription), use [manual_subtitles.py](manual_subtitles.py) as is or as the example to start from: it fetches only human-written subtitles (`get_ytdlp_content_srt(..., automatic_sub=False)`), stores them as `ytdlp_subtitles` files and never turns them into words or sentences. Only one subtitles file per content is registered: the last language fetched (the file of an earlier language stays on disk, unregistered). Never fetch automatic subtitles, and never store subtitles in `words`, `sentences` or `transcriptions`.

## Example: speaking time per speaker of a channel's diarized videos, as CSV

Saved as `jobs/<today>_speaking_time.py` and run with `python3.11 jobs/<today>_speaking_time.py`:

```python
import csv, io, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT); sys.path.insert(0, str(ROOT))
import helpers.database as db
from helpers.speakers import describe_label

UPLOADER = "psyhodelik"          # substring of the channel name
OUTPUT = Path("exports") / Path(__file__).stem / "speaking_time.csv"  # exports/<today>_speaking_time/

rows = io.StringIO()
writer = csv.writer(rows)
writer.writerow(["content_id", "title", "speaker", "who", "minutes"])
exported = 0
with db.connect() as db_conn:
    contents = db.find_contents(db_conn, uploader=UPLOADER)
    for content in contents:
        row = db_conn.execute("SELECT MAX(id) FROM diarizations WHERE content_id = ?", (content.id,)).fetchone()
        if row[0] is None:
            continue  # not diarized
        exported += 1
        labels = db.get_speaker_labels(db_conn, row[0])
        totals = {}
        for turn in db.get_turns(db_conn, row[0], exclusive=True):
            totals[turn.speaker] = totals.get(turn.speaker, 0) + turn.end_ms - turn.start_ms
        for speaker, ms in sorted(totals.items()):
            writer.writerow([content.id, content.title, speaker, describe_label(labels, speaker), round(ms / 60000, 1)])
db.write_atomic(OUTPUT, rows.getvalue())
print(f"Wrote {OUTPUT} ({exported} diarized contents of the {len(contents)} found for '{UPLOADER}')")
```

## Conventions

- Every step is idempotent: check what is stored before computing, store (and register files) only after success. Re-running a script must resume, never redo or duplicate.
- Files registered in the database are relative to the repository root and live under `cache/<file_type>/<platform>_<external_id>.<ext>`. The database keeps one path per content, file type and `variant`: when a content can have several files of one type, give each its own `variant` and put it in the file name (e.g. `cache/pyannote_result/<platform>_<external_id>_<diarization_id>.json`, variant = the diarization id).
- Write to the database only through `helpers.database` functions. **Never** `DELETE`, `UPDATE` or `DROP` by hand, and never change the schema: a new table needs a new entry appended to `MIGRATIONS` in `helpers/migrations.py`, its queries in a new module of `helpers/tables/` and a `from helpers.tables.<module> import *` line in `helpers/database.py`, then `python3.11 -m unittest discover tests` must pass; ask the user before adding one.
- Never delete or move `cache/`, `cache/test.db` or `cache/backups/`.
- Long scripts print one line per content with a `(i/n)` prefix and end with a summary of failures, then exit with `report.exit_code()` folded as above, or with 1 if anything failed in a script without `run_steps`.

## Test before the real run

1. `python3.11 -m py_compile <script>.py` must print nothing.
2. Run it on one content first (e.g. `db.find_contents(db_conn, ids=[<one id>])`), check its output and the database with the `query-database` skill.
3. Then run it on everything, in the background with a log if it takes more than a few minutes: `nohup python3.11 -u <script>.py >> jobs/<today>_<short_name>.log 2>&1 &` (in Claude Code you can instead run `python3.11 -u <script>.py >> jobs/<today>_<short_name>.log 2>&1` with `run_in_background`, which reports the exit code; a script prints none). `jobs/*.log` is git-ignored, like `cache/` and `exports/`; a log anywhere else shows up in `git status`.
