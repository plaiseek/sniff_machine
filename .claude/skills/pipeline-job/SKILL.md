---
name: pipeline-job
description: Downloads (the full videos, kept on the video disk, or the audio only), transcribes and/or diarizes (who speaks when) the videos or audios of a channel, playlist or show, or of single URLs, optionally only those uploaded between two dates, and/or computes the voice embeddings of their speakers and/or splits their transcriptions into sentences, by copying jobs/job_template.py into a job script and running it. Use for requests like "fetch, transcribe and diarize all videos from channel X uploaded between Y and Z", "transcribe these videos", "diarize the March videos of X" (in French télécharger, transcrire, diariser les vidéos d'une chaîne), or to resume or check such a job; also downloads the full videos of contents already in the database ("download the videos of everything already transcribed").
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
| `STEPS` | `"video"` in every job that fetches, downloads or gets (télécharger, récupérer), transcribes or diarizes: the user keeps the full video of each content (at most 720p, on the video disk behind `cache/ytdlp_video`), and its audio is made from it, with no mp3 downloaded; leave it out only when the request asks for the audio only, and then put `"download"` (the audio) for fetch / download / get. `"transcribe"` for transcribe / transcript / text (transcrire), `"diarize"` for diarize / speakers / who speaks when (diariser, qui parle), `"embed"` for voice embeddings / identify the speakers later (empreintes vocales): it needs `"diarize"` too, unless the contents are diarized already; `"split"` for sentences / search what was said / who said what (phrases, qui a dit quoi): it needs `"transcribe"` (and `"diarize"` for the speakers) too, unless done already. The words of the transcriptions are stored in the database by any job that transcribes, diarizes or splits (`"split"` is not needed for that) |
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

Skip this step when `STEPS` has no GPU step (only `"download"` and/or `"video"`). Otherwise run it with only the GPU steps of `STEPS` (`transcribe`, `diarize`, `embed`, `split`; never `download` or `video`, which it rejects):

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
STEPS = ["video", "transcribe", "diarize"]
LANGUAGE = "fr"
DIARIZATION_PARAMS = {}
DRY_RUN = True
```

## 4. Dry run

```bash
python3.11 jobs/<file>.py
```

It prints the name of each listing (e.g. `<channel> - Videos: 19 entries to check`), one line per selected content (id, upload date, duration, what is already done), `N contents selected`, the entries it skipped and why, and ends with `Job finished with exit code N`. Check that the listing is the channel the user meant and that every date is inside the bounds. The first listing of a channel fetches the details of each of its videos not in the database yet (about 15 s each: the requests about videos to one site are spaced 10 to 20 s apart, so that it does not rate-limit this machine; cached afterwards): with no dates, a channel of 1,000 new videos takes about 4 hours, and 40 new entries already take 10 minutes, so when the listing has many entries to check, run that dry run in the background with a log as in step 5 (in Claude Code, a foreground command stops after 10 minutes at most; what was fetched is kept, so running it again goes faster).

- Exit code 0 and at least one content selected: go on with step 5.
- Exit code 1: read the lines above it (`N listing entries or URLs were skipped (K errors), see above.` counts them).
  - `Cannot list <url>: ...`: the listing itself failed. If it says `is a single content, not a listing`, move the URL to `CONTENT_URLS`. If it is a network or rate-limit error (`HTTP Error 429`, `timed out`, `Unable to download`, `not a bot`, `try again later`), run the dry run once more. Otherwise (`HTTP Error 404`, `does not exist`, `Unsupported URL`), or if it fails again, follow the URL table: stop and ask the user for the right URL.
  - `Cannot fetch <url>:` (a single URL): `is a playlist, not a single content`: move it to `LISTING_URLS`; `is live or upcoming`: tell the user to run the job again once it is over; otherwise, as for skipped entries ending with `(error: run the job again to retry)` (network error, rate limit): run the dry run once more, and if they still fail, go on and mention them in your report.
  - A final line `Job finished with exit code 1: crashed (...)`: report the error above it to the user.
- Exit code 2 (`Invalid settings`): fix the settings lines it names and run again. Except `STEPS has "video", but the videos have nowhere to go: ...`: the video disk is not mounted (`... is its disk mounted?`) or `cache/ytdlp_video` was never set up (`... does not exist`). That is not a settings mistake: stop and give the user that line. Never create `cache/ytdlp_video` yourself (it would fill the disk of `cache/`), and never remove `"video"` from `STEPS` unless the user says to go on with the audio only. Likewise `Nothing can run: cache/<folder> links to '...', which does not exist: is its disk mounted?`: the data disk holding that folder is not mounted (after a reboot, it is mounted when the user logs in to the desktop): stop and tell the user; never create or replace that folder.
- Exit code 3 (`is already running`): the real job is running: do not start it again, go to the checks of step 5.
- No `Job finished with exit code` line (exit code 1), but a Python error: a line you edited is broken. A `SyntaxError` (a missing quote, or a `"""`, `\U`, `\N` or `\x` in the docstring) is reported at line 1 or 8 with no `Traceback` line; a `NameError` or another error comes after a `Traceback` line. Fix it and run again.
- `sentences` = `yes` with `diarization` = `no` are sentences without speakers: when the request diarizes such contents, keep `"split"` in `STEPS` so that they are split again with the speakers.
- `0 contents selected` with no `Cannot list` line: no upload between the dates, or a tab the channel does not have (`... does not have a ... tab: nothing to list`): check the dates and the URL, then tell the user instead of guessing further.
- A skipped line `<listing URL> : N entries not checked, the platform refuses this machine for now (...): run again later`: YouTube (or another site) blocked or rate-limited this machine during the listing, which then stopped asking (the entries already in the database are still selected). Do not run the dry run again now: tell the user, with the reason in parentheses, and run it again later: an hour or more after a rate limit, the next day after `not a bot` (see exit code 6 in step 6).
- Skipped entries `members only`, `premium only`, `needs an account`, `private`, `private video`, `deleted video`, `upload date unknown`, or with yt-dlp's own message (removed, geo-blocked, ...), cannot be downloaded: just list them in your report. `live now` / `upcoming: run the job again once it is over`: tell the user to run the job again once it is over. `a playlist, not a content: list it separately`: tell the user, it can be added to `LISTING_URLS`.

## 5. Real run

Set `DRY_RUN = False` in the job file. Check that it is not already running: `python3.11 jobs/<file>.py --status` must print `NOT RUNNING` (if it prints `RUNNING`, do not start it again: go to the checks below). Then run it in the background, appending to its log (`>>`: a log is never overwritten), because it can take hours (transcription and diarization each take minutes per hour of audio; with `"video"`, each video downloads before its audio, about 40 s and 0.25 to 0.7 GB per hour of video); `-u` writes each line to the log at once:

```bash
nohup python3.11 -u jobs/<file>.py >> jobs/<file>.log 2>&1 &
```

(In Claude Code you can instead run `python3.11 -u jobs/<file>.py >> jobs/<file>.log 2>&1` with `run_in_background`.) Check the progress every few minutes with `tail -n 20 jobs/<file>.log`; no new line for a while (e.g. while an audio downloads) is normal as long as `--status` prints `RUNNING`. The job is finished when the last line of the log starts with `Job finished with exit code` and `python3.11 jobs/<file>.py --status` prints `NOT RUNNING`. A `Job finished with exit code 3` line means a second copy was started while the job was running: that copy stopped at once, the first one goes on, keep waiting. If the final line does not come and `python3.11 jobs/<file>.py --status` prints `NOT RUNNING`, the job was killed (e.g. out of memory or a reboot): run it again, it resumes. Do not run two jobs on the same contents at the same time (the `work-status` skill says what is running).

## 6. Check and report

Read the end of the log (`tail -n 40 jobs/<file>.log`; the lines named below come before the final table of one line per content). Its last line gives the exit code: 0 everything done; 1 some contents failed (or the job crashed: the error is just above); 2 invalid settings; 3 already running; 4 no server of a step can be used, nothing done: the line above `Nothing done: ...` says why. `No <Kind> server of [...] is ready`: stop and tell the user which kind of server is down; `No <Kind> server answered /health within 600 s (busy: [...])`: the servers are working for another job: run the job again later, do not restart them; `The <Kind> servers run different models`: tell the user. Never edit `servers.py` or restart servers for it. 5 a server is probably stuck (the line above names it): tell the user it needs a restart, do not run the job again and do not restart it yourself; 6 downloads stopped: the line above it, `Downloads from <platform> stopped (<why>): N contents left without their download. ...`, says why, and what to do depends on it: a rate limit (`rate-limited`, `try again later`, `HTTP Error 429`, `Too Many Requests`): the platform refuses this machine for a while, do not run the job again before an hour or more, and tell the user; a bot check (`not a bot`, `captcha`): it lasts hours (from 2 to more than 12 h on YouTube after an hour of downloads at full speed) and longer when the platform is asked again: tell the user, do not run the job again before the next day, and then first check with one request, `python3.11 -m yt_dlp --simulate --print title <URL of a video>`, which prints the title once the check is lifted (`not a bot` otherwise); `3 download errors in a row, the last: ...` with `timed out`, `Unable to download`, `Connection` or another network error: check the network (`curl -sI https://www.youtube.com | head -1`) and tell the user; with `HTTP Error 403` on every content: yt-dlp may be outdated, tell the user (`python3.11 -m pip install -U yt-dlp` is their decision), do not run the job again in a loop. The contents that did not need a download went through their steps; 130 interrupted: run it again.

- `Warning: <Kind> server <address> is not ready, it is left out: <reason>` lines (at the start of the run) name the servers the job did not use; it worked with the others (`<Kind>: N of M servers used: ...`). Report them.
- If some contents failed (exit code 1: `Failed ...` lines, or `No <step>, no server of the step was left (re-run to retry):` when every server of a step stopped answering), run the same job once more: it only redoes what is missing. If the same contents fail again, report them with their error lines; do not change any code. A transcription that fails again with `500 Server Error: Internal Server Error for url: http://<address>/inference` may have no speech in its audio (whisper.cpp fails when its voice detection finds nothing): report it as such.
- `Failed video downloads:` lines: the video of these contents could not be downloaded; their audio was downloaded instead when a step needed it, so the other steps went on. Run the job once more: it only downloads the missing videos. `Only N GB left on the disk of cache/ytdlp_video: video not downloaded`: the video disk is nearly full (less than 20 GB left): tell the user, and do not run the job again with `"video"` before they free space or say to go on without the videos.
- `GPU out of memory` (from any server, e.g. `Server said: {"error":"GPU out of memory: lower 'batch_size' or 'max_duration'."}`): the GPU is shared by the model servers of its machine and their work. Report the contents and the GPU memory used (the `work-status` skill), and tell the user: running the job again helps only once memory is freed, which is their decision (e.g. fewer servers on that GPU). Never change `batch_size` or `max_duration` in the code.
- A job that transcribes, diarizes or splits also stores the words of the transcriptions in the database (a `Words: ... failures.` line). `Content N: 0 words stored` means an empty transcription: report it as transcribed, nothing heard (e.g. music), not as a failure. A failure there (exit code 1) names the content and its error: report it, do not change any code.
- Report to the user: the job file, the number of contents selected, how many are now downloaded / with their video / transcribed / diarized / with voice embeddings / split into sentences (the final table's `audio`, `video`, `transcript`, `diarization`, `voices`, `sentences` columns; `-` there means nothing to work on: not diarized, or no words; `audio` = `no` for a content already transcribed and diarized is not a failure: a job does not download its audio again unless `STEPS` has `"download"` and no GPU step; `video` = `no` for a job without `"video"` is not one either), the failures and skipped entries with their reasons, the servers left out.

To answer questions about the results afterwards (speakers, turns, what was said and by whom, which videos are done), use the `query-database` skill. Running the job again with `DRY_RUN = True` also shows what is done for each content, once `--status` prints `NOT RUNNING` (otherwise it stops with exit code 3); set `DRY_RUN = False` again before resuming the job.

## Videos of contents already in the database

To download only the full videos of contents that are already in the database (e.g. transcribed before the video disk was set up), no job is needed:

```bash
python3.11 pipeline/download_videos.py --dry-run                 # every content: how many videos, how many hours
python3.11 pipeline/download_videos.py --dry-run 243 418         # only these contents (ids)
python3.11 pipeline/download_videos.py --dry-run --uploader <part of the channel name> --from 2026-03-01 --to 2026-03-31
```

The dry run prints `N videos to download (H h), M already there; F GB free on the disk of cache/ytdlp_video.` Each hour of video takes 0.25 to 0.7 GB: if the hours times 0.7 GB come near the free space, tell the user before downloading. Then run the same command without `--dry-run`, in the background with a log when there are more than a few videos (`python3.11 -u pipeline/download_videos.py ... >> jobs/<today>_videos_<short_name>.log 2>&1`), and read its end: `Summary: N contents, ... done now: K video downloads, F failures[, S not tried (downloads stopped)].`, then `Finished with exit code N.`: 0 done, 1 some videos failed (the `Failed video downloads:` lines: run it again once, then report those still failing), 2 `Nothing downloaded: ...` (the video or data disk: as for exit code 2 of a job above, stop and tell the user), 6 the downloads were stopped (the `Downloads from <platform> stopped (<why>)` line: as for exit code 6 of a job above, never run it again right away). It downloads only the videos, oldest upload first (`--newest-first`: the latest first, e.g. when the recent videos matter most and the disk cannot hold them all), only those of a YouTube category or with a tag with `--category NAME`, `--skip-category NAME` and `--tag TAG` (repeatable, case-insensitive; the categories and tags are those the uploader set on YouTube, e.g. `--skip-category Gaming`; the dry run's first line counts the contents kept), at most 2 MB/s (7 GB per hour: at full speed, YouTube's bot check refused this machine after about an hour; `--max-rate 1.5` lowers it, `--max-rate 0` lifts it: only when the user asks); running it again skips those already there. With `--cookies youtube_cookies.txt`, the downloads are signed in to a YouTube account, which gets past `not a bot`: only when the user gave that file (the cookies of a throwaway account, exported from a private window: YouTube may ban an account that downloads this much). It is git-ignored: never print, copy or commit it. Do not run it while a job downloads from the same site: the spacing of the requests holds within one script only, two scripts ask twice as fast.

## Rules

- For a job, only create or edit files in `jobs/`. Never edit `helpers/`, `servers.py` or other scripts.
- Never delete or move `cache/`, `cache/test.db` or `cache/backups/`: they cannot be recomputed.
- Re-running a job is safe (what is already in the database is skipped), except while it is running (`--status` prints `RUNNING`), after exit code 5 (a server needs a restart first), and right after exit code 6 (wait as step 6 says).
- Stopping a job (`kill <pid>`) is safe too: run it again later to resume.
- A job script takes only `--status`: any other argument (even `--help`) runs the job.
