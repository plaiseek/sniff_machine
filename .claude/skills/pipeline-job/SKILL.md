---
name: pipeline-job
description: Downloads, transcribes and/or diarizes (who speaks when) the videos or audios of a channel, playlist or show, or of single URLs, optionally only those uploaded between two dates, and splits their transcriptions into sentences, by copying jobs/job_template.py into a job script and running it. Use for requests like "fetch, transcribe and diarize all videos from channel X uploaded between Y and Z", "transcribe these videos", "diarize the March videos of X" (in French télécharger, transcrire, diariser les vidéos d'une chaîne), or to resume or check such a job.
---

# Pipeline job

Do the steps in order. Run every command from the repository root (the folder with `servers.py`).

## 1. Turn the request into settings

Fill in every row before writing any file:

| Setting | Value |
|---|---|
| `LISTING_URLS` | channel / playlist / show URLs, written as in the URL table below |
| `CONTENT_URLS` | single video URLs given in the request (`watch?v=...`, `twitch.tv/videos/...`) |
| `UPLOADED_FROM`, `UPLOADED_TO` | `"YYYY-MM-DD"`, both inclusive, or `None` when the request gives no bound |
| `STEPS` | `"download"` for fetch / download / get (télécharger, récupérer), `"transcribe"` for transcribe / transcript / text (transcrire), `"diarize"` for diarize / speakers / who speaks when (diariser, qui parle), `"embed"` for voice embeddings / identify the speakers later (empreintes vocales): it needs `"diarize"` too, unless the contents are diarized already; `"split"` for sentences / search what was said / who said what (phrases, qui a dit quoi): it needs `"transcribe"` (and `"diarize"` for the speakers) too, unless done already. The words of the transcriptions are stored in the database by any job that transcribes or diarizes, without `"split"` |
| `LANGUAGE` | `"fr"`, unless the request says the videos are in another language (`"en"`, ..., or `"auto"`) |
| `DIARIZATION_PARAMS` | `{}`, unless the request gives the number of speakers: `{"num_speakers": 2}` or `{"min_speakers": 2, "max_speakers": 5}` |

URL table:

| The request names | Put in `LISTING_URLS` |
|---|---|
| a YouTube channel handle: `@name`, `youtube.com/@name` | `"https://www.youtube.com/@name/videos"` |
| a YouTube channel by its name only ("la chaîne Le Monde") | try `"https://www.youtube.com/@<name without spaces or accents>/videos"`, and check in the dry run that the listing's name is that channel; if the listing fails or is another channel, stop and ask the user for the channel's URL |
| ... and its past live streams too | add `"https://www.youtube.com/@name/streams"` |
| `youtube.com/channel/UC...` | `"https://www.youtube.com/channel/UC.../videos"` |
| a Twitch channel: `twitch.tv/name`, "the Twitch channel name" | `"https://www.twitch.tv/name/videos?filter=archives"` |
| a playlist or a Radio France show page | the URL, unchanged |

Dates (`UPLOADED_FROM` / `UPLOADED_TO`), compared with the upload date in UTC:

| The request says | `UPLOADED_FROM` | `UPLOADED_TO` |
|---|---|---|
| between 2026-03-01 and 2026-03-15 | `"2026-03-01"` | `"2026-03-15"` |
| in March 2026 (en mars 2026) | `"2026-03-01"` | `"2026-03-31"` |
| since June 2026 (depuis juin 2026) | `"2026-06-01"` | `None` |
| before 2025 (avant 2025) | `None` | `"2024-12-31"` |
| no date ("all videos") | `None` | `None` |

For a relative period, do not compute the dates yourself: run the script below and copy the two lines it prints into the job. Say in your report which dates you used.

| The request says | Command |
|---|---|
| last week (la semaine dernière: the previous Monday to Sunday) | `python3.11 .claude/skills/pipeline-job/dates.py last-week` |
| this week (cette semaine) | `python3.11 .claude/skills/pipeline-job/dates.py this-week` |
| last month (le mois dernier) / this month (ce mois-ci) | `python3.11 .claude/skills/pipeline-job/dates.py last-month` / `... this-month` |
| the last 30 days (les 30 derniers jours), today included | `python3.11 .claude/skills/pipeline-job/dates.py last-30-days` |
| yesterday (hier) / today (aujourd'hui) | `python3.11 .claude/skills/pipeline-job/dates.py yesterday` / `... today` |
| a whole month or year | `python3.11 .claude/skills/pipeline-job/dates.py 2026-03` / `... 2025` |

**Stop and ask the user** when: you cannot tell which channel or URL is meant; a date is ambiguous (e.g. `03/04/2026`); the request asks for something these steps do not do (naming the speakers is the `speaker-identity` skill, after a job with `"diarize"` and `"embed"`); the request asks for subtitles (not part of a job: only those written by people, see the `write-pipeline-script` skill); the request asks to transcribe again contents already transcribed (e.g. in another language): a job never redoes a transcription, whatever `LANGUAGE` says. (Diarizing again with other `DIARIZATION_PARAMS` is supported: each set of params is a separate diarization.)

## 2. Check the servers

Skip this step when `STEPS` is only `["download"]`. Otherwise run, with only the steps you need:

```bash
python3.11 model_servers/check_servers.py transcribe diarize embed split
```

Every line must say `OK`, `BUSY` or `LOADING`. If a line says `DOWN`, stop and tell the user which address is down: the servers run on the GPU machines (see the `model-servers` skill). Do not edit `servers.py` unless the user gives you new addresses.

## 3. Create the job script

```bash
cp jobs/job_template.py jobs/<today>_<short_name>.py
```

`<today>` is `date +%F`; `<short_name>` uses only lowercase letters, digits and `_` (e.g. `jobs/2026-10-25_psyhodelik_march.py`). In the copy, edit **only** the settings lines between the two `=====` markers, and replace the first docstring line with the request. Leave `DRY_RUN = True`.

Example, for "fetch, transcribe and diarize all videos from channel @psyhodelik uploaded between 2026-03-01 and 2026-03-31":

```python
LISTING_URLS = ["https://www.youtube.com/@psyhodelik/videos"]
CONTENT_URLS = []
UPLOADED_FROM = "2026-03-01"
UPLOADED_TO = "2026-03-31"
STEPS = ["download", "transcribe", "diarize"]
LANGUAGE = "fr"
DIARIZATION_PARAMS = {}
DRY_RUN = True
```

## 4. Dry run

```bash
python3.11 jobs/<file>.py
```

It prints the name of each listing (e.g. `Sniff Demo - Videos: 19 entries to check`), one line per selected content (id, upload date, duration, what is already done), `N contents selected`, the entries it skipped and why, and ends with `Job finished with exit code N`. Check that the listing is the channel the user meant and that every date is inside the bounds. The first listing of a channel fetches the details of each of its videos (about 1 to 3 s each, cached afterwards): it can take minutes.

- Exit code 0: go on with step 5.
- Exit code 1: some entries could not be checked (network error, rate limit): run the dry run once more; if they still fail, go on and mention them in your report.
- Exit code 2 (`Invalid settings`): fix the settings lines it names and run again.
- No `Job finished with exit code` line, but a Python `Traceback` (e.g. `SyntaxError`, `NameError`): a line you edited is broken (a missing quote, a `"` inside the docstring): fix it and run again.
- `0 contents selected`: check the URL (YouTube needs the `/videos` tab) and the dates, then tell the user instead of guessing further.
- Skipped entries marked members only, upcoming, live, private or removed cannot be downloaded: just list them in your report.

## 5. Real run

Set `DRY_RUN = False` in the job file. Check that it is not already running: `python3.11 jobs/<file>.py --status` must print `NOT RUNNING` (if it prints `RUNNING`, do not start it again: go to the checks below). Then run it in the background, appending to its log (`>>`: a log is never overwritten), because it can take hours (transcription and diarization each take minutes per hour of audio):

```bash
nohup python3.11 jobs/<file>.py >> jobs/<file>.log 2>&1 &
```

(In Claude Code you can instead run `python3.11 jobs/<file>.py >> jobs/<file>.log 2>&1` with `run_in_background`.) Check the progress every few minutes with `tail -n 20 jobs/<file>.log`. The job is finished when the last line of the log starts with `Job finished with exit code` and `python3.11 jobs/<file>.py --status` prints `NOT RUNNING`. A `Job finished with exit code 3` line means a second copy was started while the job was running: that copy stopped at once, the first one goes on, keep waiting. If the final line does not come and `python3.11 jobs/<file>.py --status` prints `NOT RUNNING`, the job was killed (e.g. out of memory or a reboot): run it again, it resumes. Do not run two jobs on the same contents at the same time.

## 6. Check and report

Read the end of the log (`tail -n 40 jobs/<file>.log`). Its last line gives the exit code: 0 everything done; 1 some contents failed (or the job crashed: the error is just above); 2 invalid settings; 3 already running; 4 a server is not ready, nothing done: stop and tell the user which server (the lines above say it), do not edit `servers.py` or restart servers; 5 a server is probably stuck (the line above names it): tell the user it needs a restart, do not run the job again and do not restart it yourself; 130 interrupted: run it again.

- If some contents failed (`Failed ...` lines, exit code 1), run the same job once more: it only redoes what is missing. If the same contents fail again, report them with their error lines; do not change any code.
- Report to the user: the job file, the number of contents selected, how many are now downloaded / transcribed / diarized / split into sentences, the failures and skipped entries with their reasons.
- A job that transcribes, diarizes or splits also stores the words of the transcriptions in the database (a `Words: ... failures.` line). A failure there (exit code 1) names the content and its error: report it, do not change any code.

To answer questions about the results afterwards (speakers, turns, what was said and by whom, which videos are done), use the `query-database` skill. Running the job again with `DRY_RUN = True` also shows what is done for each content.

## Rules

- For a job, only create or edit files in `jobs/`. Never edit `helpers/`, `servers.py` or other scripts.
- Never delete or move `cache/`, `cache/test.db` or `cache/backups/`: they cannot be recomputed.
- Re-running a job is always safe: what is already in the database is skipped.
- Stopping a job (`kill <pid>`) is safe too: run it again later to resume.
