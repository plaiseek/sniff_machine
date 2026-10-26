---
name: pipeline-job
description: Downloads, transcribes and/or diarizes (who speaks when) the videos or audios of a channel, playlist or show, or of single URLs, optionally only those uploaded between two dates, and/or computes the voice embeddings of their speakers and/or splits their transcriptions into sentences, by copying jobs/job_template.py into a job script and running it. Use for requests like "fetch, transcribe and diarize all videos from channel X uploaded between Y and Z", "transcribe these videos", "diarize the March videos of X" (in French télécharger, transcrire, diariser les vidéos d'une chaîne), or to resume or check such a job.
---

# Pipeline job

Do the steps in order. Run every command from the repository root (the folder with `servers.py`).

## 1. Turn the request into settings

Fill in every row before writing any file:

| Setting | Value |
|---|---|
| `LISTING_URLS` | channel / playlist / show URLs, written as in the URL table below |
| `CONTENT_URLS` | single video URLs given in the request (`watch?v=...`, `twitch.tv/videos/...`) |
| `UPLOADED_FROM`, `UPLOADED_TO` | `"YYYY-MM-DD"`, both inclusive, or `None` when the request gives no bound. Dates only filter `LISTING_URLS`: with only `CONTENT_URLS`, leave both `None` (otherwise the job exits 2) |
| `STEPS` | `"download"` for fetch / download / get (télécharger, récupérer), `"transcribe"` for transcribe / transcript / text (transcrire), `"diarize"` for diarize / speakers / who speaks when (diariser, qui parle), `"embed"` for voice embeddings / identify the speakers later (empreintes vocales): it needs `"diarize"` too, unless the contents are diarized already; `"split"` for sentences / search what was said / who said what (phrases, qui a dit quoi): it needs `"transcribe"` (and `"diarize"` for the speakers) too, unless done already. The words of the transcriptions are stored in the database by any job that transcribes, diarizes or splits (`"split"` is not needed for that) |
| `LANGUAGE` | `"fr"`, unless the request says the videos are in another language (`"en"`, ..., or `"auto"`) |
| `DIARIZATION_PARAMS` | `{}`, unless the request gives the number of speakers: `{"num_speakers": 2}` or `{"min_speakers": 2, "max_speakers": 5}` |

URL table:

| The request names | Put in `LISTING_URLS` |
|---|---|
| a YouTube channel handle: `@name`, `youtube.com/@name` | `"https://www.youtube.com/@name/videos"` |
| a YouTube channel by its name only ("la chaîne Le Monde") | try `"https://www.youtube.com/@<name without spaces or accents>/videos"`, and check in the dry run that the listing's name is that channel; if the listing fails or is another channel, stop and ask the user for the channel's URL |
| ... and its past live streams too | add `"https://www.youtube.com/@name/streams"` |
| `youtube.com/channel/UC...`, `youtube.com/c/<name>`, `youtube.com/user/<name>` | the same URL followed by `/videos` |
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

For a relative period, do not compute the dates yourself: run the script below and copy the two `UPLOADED_` lines it prints into the job (the comment line above them gives the day it took as today). Say in your report which dates you used.

| The request says | Command |
|---|---|
| last week (la semaine dernière: the previous Monday to Sunday) | `python3.11 .claude/skills/pipeline-job/dates.py last-week` |
| this week (cette semaine) | `python3.11 .claude/skills/pipeline-job/dates.py this-week` |
| last month (le mois dernier) / this month (ce mois-ci) | `python3.11 .claude/skills/pipeline-job/dates.py last-month` / `... this-month` |
| the last 30 days (les 30 derniers jours), today included; the last 7 days, the last N days | `python3.11 .claude/skills/pipeline-job/dates.py last-30-days` / `... last-7-days` / `... last-N-days` |
| yesterday (hier) / today (aujourd'hui) | `python3.11 .claude/skills/pipeline-job/dates.py yesterday` / `... today` |
| this year / last year (cette année / l'année dernière) | `python3.11 .claude/skills/pipeline-job/dates.py this-year` / `... last-year` |
| a whole month or year | `python3.11 .claude/skills/pipeline-job/dates.py 2026-03` / `... 2025` |

**Stop and ask the user** when: you cannot tell which channel or URL is meant; a date is ambiguous (e.g. `03/04/2026`); the request asks for something these steps do not do (naming the speakers is the `speaker-identity` skill, after a job with `"diarize"` and `"embed"`); the request asks for subtitles (not part of a job: only those written by people, see the `write-pipeline-script` skill); the request asks to transcribe again contents already transcribed (e.g. in another language): a job never redoes a transcription, whatever `LANGUAGE` says. (Diarizing again with other `DIARIZATION_PARAMS` is supported: each set of params is a separate diarization.)

## 2. Check the servers

Skip this step when `STEPS` is only `["download"]`. Otherwise run it with only the GPU steps of `STEPS` (`transcribe`, `diarize`, `embed`, `split`; never `download`, which it rejects):

```bash
python3.11 model_servers/check_servers.py transcribe diarize embed split
```

It prints one line per server of these kinds (`transcribe` = `whisper`, `diarize` = `pyannote`, `embed` = `speechbrain`, `split` = `wtpsplit`): `OK`, `BUSY`, `LOADING` or `DOWN (...)`, and `N of M up (the others are left out)` for a kind with some servers down. A job leaves out the servers that do not answer and works with the others. It exits 1 with `No server up for: <kinds>` when a kind has no server up: then stop and tell the user (the servers run on the GPU machines, see the `model-servers` skill). Otherwise go on, and name the `DOWN` addresses in your report. Do not edit `servers.py` unless the user gives you new addresses.

## 3. Create the job script

```bash
cp jobs/job_template.py jobs/<today>_<short_name>.py
```

`<today>` is `date +%F`; `<short_name>` uses only lowercase letters, digits and `_` (e.g. `jobs/2026-10-25_psyhodelik_march.py`). In the copy, edit **only** the settings lines between the two `=====` markers, and replace `<write the request here, ...>` on the first line with the request (keep `"""Job: ` before it, and leave any `"""` or backslash `\` out of it). Leave `DRY_RUN = True`.

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

It prints the name of each listing (e.g. `<channel> - Videos: 19 entries to check`), one line per selected content (id, upload date, duration, what is already done), `N contents selected`, the entries it skipped and why, and ends with `Job finished with exit code N`. Check that the listing is the channel the user meant and that every date is inside the bounds. The first listing of a channel fetches the details of each of its videos (about 1 to 3 s each, cached afterwards): with no dates, a channel of 1,000 videos takes up to an hour, so run that dry run in the background with a log as in step 5 (in Claude Code, a foreground command stops after 10 minutes at most; what was fetched is kept, so running it again goes faster).

- Exit code 0 and at least one content selected: go on with step 5.
- Exit code 1: read the lines above it (`N listing entries or URLs were skipped (K errors), see above.` counts them).
  - `Cannot list <url>: ...`: the listing itself failed. If it says `is a single content, not a listing`, move the URL to `CONTENT_URLS`. If it is a network or rate-limit error (`HTTP Error 429`, `timed out`, `Unable to download`, `not a bot`, `try again later`), run the dry run once more. Otherwise (`HTTP Error 404`, `does not exist`, `Unsupported URL`), or if it fails again, follow the URL table: stop and ask the user for the right URL.
  - `Cannot fetch <url>:` (a single URL): `is a playlist, not a single content`: move it to `LISTING_URLS`; `is live or upcoming`: tell the user to run the job again once it is over; otherwise, as for skipped entries ending with `(error: run the job again to retry)` (network error, rate limit): run the dry run once more, and if they still fail, go on and mention them in your report.
  - A final line `Job finished with exit code 1: crashed (...)`: report the error above it to the user.
- Exit code 2 (`Invalid settings`): fix the settings lines it names and run again.
- Exit code 3 (`is already running`): the real job is running: do not start it again, go to the checks of step 5.
- No `Job finished with exit code` line (exit code 1), but a Python error: a line you edited is broken. A `SyntaxError` (a missing quote, or a `"""`, `\U`, `\N` or `\x` in the docstring) is reported at line 1 or 8 with no `Traceback` line; a `NameError` or another error comes after a `Traceback` line. Fix it and run again.
- `sentences` = `yes` with `diarization` = `no` are sentences without speakers: when the request diarizes such contents, keep `"split"` in `STEPS` so that they are split again with the speakers.
- `0 contents selected` with no `Cannot list` line: no upload between the dates, or a tab the channel does not have (`... does not have a ... tab: nothing to list`): check the dates and the URL, then tell the user instead of guessing further.
- Skipped entries `members only`, `premium only`, `needs an account`, `private`, `private video`, `deleted video`, `upload date unknown`, or with yt-dlp's own message (removed, geo-blocked, ...), cannot be downloaded: just list them in your report. `live now` / `upcoming: run the job again once it is over`: tell the user to run the job again once it is over. `a playlist, not a content: list it separately`: tell the user, it can be added to `LISTING_URLS`.

## 5. Real run

Set `DRY_RUN = False` in the job file. Check that it is not already running: `python3.11 jobs/<file>.py --status` must print `NOT RUNNING` (if it prints `RUNNING`, do not start it again: go to the checks below). Then run it in the background, appending to its log (`>>`: a log is never overwritten), because it can take hours (transcription and diarization each take minutes per hour of audio); `-u` writes each line to the log at once:

```bash
nohup python3.11 -u jobs/<file>.py >> jobs/<file>.log 2>&1 &
```

(In Claude Code you can instead run `python3.11 -u jobs/<file>.py >> jobs/<file>.log 2>&1` with `run_in_background`.) Check the progress every few minutes with `tail -n 20 jobs/<file>.log`; no new line for a while (e.g. while an audio downloads) is normal as long as `--status` prints `RUNNING`. The job is finished when the last line of the log starts with `Job finished with exit code` and `python3.11 jobs/<file>.py --status` prints `NOT RUNNING`. A `Job finished with exit code 3` line means a second copy was started while the job was running: that copy stopped at once, the first one goes on, keep waiting. If the final line does not come and `python3.11 jobs/<file>.py --status` prints `NOT RUNNING`, the job was killed (e.g. out of memory or a reboot): run it again, it resumes. Do not run two jobs on the same contents at the same time (the `work-status` skill says what is running).

## 6. Check and report

Read the end of the log (`tail -n 40 jobs/<file>.log`; the lines named below come before the final table of one line per content). Its last line gives the exit code: 0 everything done; 1 some contents failed (or the job crashed: the error is just above); 2 invalid settings; 3 already running; 4 no server of a step can be used, nothing done: the line above `Nothing done: ...` says why. `No <Kind> server of [...] is ready`: stop and tell the user which kind of server is down; `No <Kind> server answered /health within 600 s (busy: [...])`: the servers are working for another job: run the job again later, do not restart them; `The <Kind> servers run different models`: tell the user. Never edit `servers.py` or restart servers for it. 5 a server is probably stuck (the line above names it): tell the user it needs a restart, do not run the job again and do not restart it yourself; 130 interrupted: run it again.

- `Warning: <Kind> server <address> is not ready, it is left out: <reason>` lines (at the start of the run) name the servers the job did not use; it worked with the others (`<Kind>: N of M servers used: ...`). Report them.
- If some contents failed (exit code 1: `Failed ...` lines, or `No <step>, no server of the step was left (re-run to retry):` when every server of a step stopped answering), run the same job once more: it only redoes what is missing. If the same contents fail again, report them with their error lines; do not change any code. A transcription that fails again with `500 Server Error: Internal Server Error for url: http://<address>/inference` may have no speech in its audio (whisper.cpp fails when its voice detection finds nothing): report it as such.
- `GPU out of memory` (from any server, e.g. `Server said: {"error":"GPU out of memory: lower 'batch_size' or 'max_duration'."}`): the GPU is shared by the model servers of its machine and their work. Report the contents and the GPU memory used (the `work-status` skill), and tell the user: running the job again helps only once memory is freed, which is their decision (e.g. fewer servers on that GPU). Never change `batch_size` or `max_duration` in the code.
- A job that transcribes, diarizes or splits also stores the words of the transcriptions in the database (a `Words: ... failures.` line). `Content N: 0 words stored` means an empty transcription: report it as transcribed, nothing heard (e.g. music), not as a failure. A failure there (exit code 1) names the content and its error: report it, do not change any code.
- Report to the user: the job file, the number of contents selected, how many are now downloaded / transcribed / diarized / with voice embeddings / split into sentences (the final table's `audio`, `transcript`, `diarization`, `voices`, `sentences` columns; `-` there means nothing to work on: not diarized, or no words; `audio` = `no` for a content already transcribed and diarized is not a failure: a job does not download its audio again unless `STEPS` is only `["download"]`), the failures and skipped entries with their reasons, the servers left out.

To answer questions about the results afterwards (speakers, turns, what was said and by whom, which videos are done), use the `query-database` skill. Running the job again with `DRY_RUN = True` also shows what is done for each content, once `--status` prints `NOT RUNNING` (otherwise it stops with exit code 3); set `DRY_RUN = False` again before resuming the job.

## Rules

- For a job, only create or edit files in `jobs/`. Never edit `helpers/`, `servers.py` or other scripts.
- Never delete or move `cache/`, `cache/test.db` or `cache/backups/`: they cannot be recomputed.
- Re-running a job is safe (what is already in the database is skipped), except while it is running (`--status` prints `RUNNING`) and after exit code 5 (a server needs a restart first).
- Stopping a job (`kill <pid>`) is safe too: run it again later to resume.
- A job script takes only `--status`: any other argument (even `--help`) runs the job.
