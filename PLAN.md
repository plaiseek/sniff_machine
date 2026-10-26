# Sniff Machine — plan

What is still to do. The review of October 2026, with its findings, the design of every phase and the implementation notes of what was built, is in [docs/history/2026-10-review.md](docs/history/2026-10-review.md) (its file names are those of that time).

**Status:** Phases 1 to 6 are implemented: the pipeline (download, transcription, diarization, voice embeddings, sentences), the database with its migrations, speaker identity, "who said what", and questions to an LLM about the transcriptions (`explore/ask_transcripts.py`), with jobs and skills for AI agents. The items below are open.

## What is good and should stay

- **One SQLite DB as the single source of truth**, with idempotent "already in DB → skip" steps.
- **One self-contained module per model server** (Dockerfile + server code + build/run/stop functions), a matching `*_client.py` and a tiny `model_servers/run_*_servers.py`. It is easy to add a server by copying one.
- **Stateless HTTP model servers** with strict `accepted_params` validation. Clients get clear 400s.
- **sqlite-vec is already loaded** (`connect()` in `helpers/database.py`). That's the right tool for voice embeddings.
- **A 16 kHz mono wav per content** (`helpers/ffmpeg.py`) is the right canonical input for pyannote, SpeechBrain and Whisper. It just needs to actually be used everywhere.

## Conventions to adopt (keep the repo simple as it grows)

1. **The DB is the to-do list.** Each processing script selects "contents that have X but not yet Y", processes them, and writes the results in one transaction. Re-running resumes where it stopped. No queue service, no orchestrator.
2. **Rows for things you query** (turns, embeddings, words, failures); **files for big raw artifacts** (audio, whisper `verbose_json`, yt-dlp info), registered in `files`.
3. **One canonical audio file per content** (the 16 kHz mono wav), sent to *every* server so that Whisper word times, pyannote turns and SpeechBrain ranges share one timeline.
4. **Servers never see DB ids, and clients never touch the DB.** Pipeline scripts do the glue.
5. **One server contract.** `GET /health` (whisper.cpp already serves it) returns `{"status": "ok", "model": ...}`. `POST /<verb>` takes a multipart `file` plus a `params` JSON field. Errors are JSON `{"error": ...}`: 4xx for bad input, 5xx for server faults. gunicorn runs with `--timeout 0`.
6. **Every script** has `argparse` with today's constants as defaults (a bare `python x.py` behaves as now), a `main()` with an `if __name__ == "__main__"` guard, and explicit imports (no `import *`). It prints a failure summary at the end and exits non-zero if anything failed.

---

## Phase 0 — Protect what works today (≈ 1 h, before any rebuild or migration)

| # | Action | Why |
|---|---|---|
| 0.1 | Move the DB out of the disposable cache: `cache/test.db` → `data/sniff.db` (`db_path` in `helpers/database.py`). Run `PRAGMA wal_checkpoint(TRUNCATE)` first, or copy with `VACUUM INTO`. | `rm -rf cache` to free disk would delete the only non-recomputable data: speaker names, enrollments, human corrections. |
| 0.2 | Add a ~10-line `backup_db.py`: `VACUUM INTO 'backups/sniff-<timestamp>.db'` (safe while other connections are open). Call it before every migration. | The Phase 3 migrations rebuild tables. |
| 0.3 | Record the **working** image configuration: `docker image inspect rocm/pytorch:latest --format '{{index .RepoDigests 0}}'`; `docker run --rm --entrypoint pip amd_pyannote:latest freeze > helpers/constraints_pyannote.txt` (same for `amd_wtpsplit`); `docker run --rm --entrypoint git amd_whisper:latest -C /app rev-parse HEAD`. | docker-py does not re-pull `latest` when a local copy exists, so your working pyannote image was built from a *cached* base. `latest` on Docker Hub has moved on, and `torchcodec v0.10.0` (`pyannote_amd_server.py:7`) only matches the torch it was built against. A fresh machine or a `docker system prune` will break the build, and the digest you have now cannot be recovered afterwards. |

Status: 0.2 is done another way: `connect()` backs the database up in `cache/backups/` before every migration, and `admin/restore_backup.py` restores a backup. 0.1 is not done: the database is still `cache/test.db` (moving it means updating the data-protection hook, `query.py` and the docs). 0.3: whisper.cpp is pinned (v1.9.5); the `rocm/pytorch` base images are still `latest`.

## Open items of the built phases

- **Phase 3:** the `failures` table (3.2); the 3.3 helpers (repo-root paths, `upsert_content`, `contents_needing`); the contents ingested before migration v2 have no `channel_id` or `series` (all 469 of them in October 2026): backfill them from their `ytdlp_info` JSON before grouping speakers per channel.
- **Phase 5:** the speaker-count error of 5.5 (it needs the true number of speakers per content); linking unknown voices across a channel's contents; the privacy and legal scope of voiceprints (GDPR art. 9) before publishing anything that names speakers.
- **Phase 6:** snapping words just outside a turn; tuning SaT's threshold for the long unpunctuated stretches; matplotlib under python3.11 needs Pillow for 3.11.
- **LLM questions:** compare the recall of the windows with a whole-transcript prompt, and tune the window size; check the first pass's recall with the criteria; `meets`/`against` as columns (migration v8) if they need filtering; prompts that reach a prefix-cache block (1,648 tokens with Qwen3.8-27B). The export for a lawyer is done (`--export`, the `findings-dossier` skill). The hard-end estimate (7.1 tokens written per word) is an average: single contents with passages to check wrote up to 8.6. `--dry-run` stores the words of new transcriptions (it needs them to count). A stuck vLLM ends in exit 1, not 5: its requests time out after 1 h, and "stuck" needs 24 h. The `--schema` comment of `findings.attribution` lists 3 of the 5 values (next migration).

## Found by the skill check of 2026-10-26 (servers on one machine)

- **whisper.cpp v1.9.5 answers HTTP 500** to `/inference` when its VAD finds no speech (container log: `Final speech segments after filtering: 0`, then `unknown language id -2`); a retry can return an empty transcription. Seen on content 465 (24 s, 3.9 s of speech by Pyannote). The client could treat it as an empty transcription, or the language detection could be skipped when `language` is given.
- **One 16 GB GPU for every server is too small**: with 2 Whisper large-v3 + Pyannote + SpeechBrain + wtpsplit on the RX 7900 GRE, about 14.3 GiB are used at rest and SpeechBrain runs out of memory (`GPU out of memory: lower 'batch_size' or 'max_duration'`): content 167 has no voice embeddings yet. Run one Whisper on such a card, or cap the servers' PyTorch memory.
- **Correcting speaker names**: no script records "this label is nobody I know" or a wrong name (the schema supports a human label with no speaker), and none finds an unnamed voice in other contents before it is named (`speakers/similar_labels.py CONTENT LABEL`, naming no one).
- A live or upcoming `CONTENT_URLS` entry is an error (exit 1), while in a listing it is skipped without one.
- A job's `--status` takes the job's lock for an instant: a job started at that instant stops with exit code 3.
- Rebuilding the Whisper image left the old one (33 GB) dangling: `docker system df` shows about 18 GB reclaimable (the user's decision).

## Phase 7 — Sources and scheduled runs (README TODO 1, M)

1. **Split downloading from GPU work.** The jobs can already: `STEPS = ["download"]` needs no GPU server, then `pipeline/transcript_pending_audios.py` and `pipeline/diarize_contents.py` process what is downloaded. What is left is running downloads on their own schedule. Sources expire (Twitch VOD retention, TV/radio replays), so downloads must not wait for the GPU box. For a first backfill, process the oldest first.
2. **`sources` table + `sniff_sources.py`** (name, url, enabled, language, min/max speakers, last_listed_at). `list_contents()` (the jobs' listing) already reads a listing page by page and stops past the upload dates asked for. For Twitch, use `/videos?filter=archives`; the default includes highlights that duplicate archive audio.
3. **French sources** (from the yt-dlp extractors on master):

   | Source | Listing | Notes |
   |---|---|---|
   | Radio France (Inter, Culture, franceinfo radio, FIP, Mouv…) | **Yes**: podcast show pages, profiles, daily programme schedules, or the show's RSS feed | Stored as platform `FranceCulture` for every station. Episodes often have only `upload_date`. RFI is not Radio France. |
   | france.tv | **No programme listing**, only `.html` episode pages | Needs a ~15-line scraper of programme pages. Geo FR; some items DRM. No `uploader`. |
   | franceinfo (site) | Single article | May resolve to a *playlist* (Dailymotion embeds). |
   | Le Figaro | **Yes** (`LeFigaroVideoSection`) | Platform `JWPlatform`. |
   | BFMTV, LCI, TF1, Le Monde, TV5MONDE | Single URLs only | LCI/TF1 → `Wat` (DRM/login likely for many TF1+ replays). TV5MONDE: geo FR, better with `curl_cffi`. |
   | LCP | Broken (`_WORKING=False`) | |
   | 20min | **Wrong site**: the extractor is the Swiss *20min.ch*, also broken | Remove from the list. |

   Probe each source once with `yt-dlp -vU --simulate -F <url>` (DRM, geo, no formats) and `yt-dlp --flat-playlist -J <listing>` before writing code for it. Record DRM and geo errors as `failures` rows.
4. **Live sources** (`bfmtv:live`, `RadioFranceLive`) don't fit "one finite file per content". If needed later, write a `record_live.py URL CHUNK_MINUTES` that records chunks with `ffmpeg -t` and registers each chunk as its own content (`external_id = f"{id}_{start_utc}"`).
5. **Be gentle with yt-dlp.** Add one shared options dict with `sleep_interval_requests` / `sleep_interval` / `max_sleep_interval` and an optional cookie file. Download from the info you already extracted (`ydl.process_ie_result(info, download=True)`) instead of extracting twice (`helpers/yt_dlp.py:22` then `:86`). **Pin everything except `yt-dlp`/`yt-dlp-ejs`**: extractors break when sites change, and the fix is always to update.
6. **Scheduling:** a `run_pipeline.sh` that runs each step in turn, wrapped in `flock -n` so cron runs never overlap. Add an optional `--detach` to `model_servers/run_*_servers.py` with `restart_policy={"Name": "unless-stopped"}`.
7. **Disk:** today the mp3 (192 kbps, ~86 MB/h) and the wav (~115 MB/h) are both kept forever. Options:
   - download `bestaudio` with no mp3 re-encode, and keep a single 16 kHz mono **FLAC** (estimated ~half the wav) as the canonical file, after checking that every server reads it;
   - add a `cleanup_cache.py` that evicts intermediate files once all steps are done. Keep the canonical audio for sources that expire.
8. **Language per source** instead of a job's single `LANGUAGE`. RFI and TV5MONDE are multilingual: use `language=auto` there and store the detected language.


## Phase 8 — Hygiene (interleave with the above)

| # | Item |
|---|---|
| 8.1 | **Done**: `requirements.txt` (datasette optional; the tests use the standard `unittest`). |
| 8.2 | **README fixes.** On Linux, use **Docker Engine**, not Docker Desktop: Desktop runs containers in a VM and cannot pass `/dev/kfd` to them. Whisper exposes `/health`, not `/ready`. If vec0 tables are ever added, datasette needs `--load-extension "$(python3 -c 'import sqlite_vec; print(sqlite_vec.loadable_path())')"`. |
| 8.3 | **Tests** (no GPU, network or Docker), **partly done**: `tests/` has the migrations of a new and of a v1 database, and `explore/ask_transcripts.py`'s question step against a fake vLLM server (`python3.11 -m unittest discover tests`). Still to add: `dtw_to_srt_timestamp`, `whisper_result_to_srt`, the DB helpers, and `compile(SERVER_SCRIPT, …)` for every server, which catches syntax errors before a long image build. Run `ruff` for lint and format. |
| 8.4 | **Reproducible, secret-free images.** Pin the base image by digest (0.3), pip versions via constraints, the whisper.cpp commit and HF model `revision=`. Add `ENV HF_HUB_OFFLINE=1` after build-time downloads: today `from_pretrained` checks the Hub at every container start, so results can change without any image change. Today the HF token is baked into an image layer and into `/app/pyannote_server.py`. Pass it at runtime (`environment={"HF_TOKEN": …}` + an HF cache volume) or download on the host. |
| 8.5 | **Readable server modules:** `SERVER_SCRIPT = r'''…'''` with configuration through `ENV`, instead of f-strings with doubled braces. Keep one file per server and the base64 injection. |
| 8.6 | Bind published ports to `127.0.0.1` unless remote GPU boxes are intended: whisper.cpp's `/load` lets anyone on the LAN swap the model. Label containers, so `stop_*` still finds them after an image rebuild. |
| 8.7 | **Done**: explicit imports everywhere (`helpers/database.py` re-exports `helpers/tables/` on purpose), `test.py` deleted, `__pycache__/` ignored everywhere. |
| 8.8 | Optional: a named volume for the MIOpen kernel cache (`~/.cache/miopen`), so the first requests after each container start aren't slow. |
