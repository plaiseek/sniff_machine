# Sniff Machine

A pipeline for automatically downloading and transcribing videos from YouTube, Twitch (and, soon, many other platforms). Everything is tracked in a SQLite database, and the heavy lifting (transcription, diarization, sentence segmentation) is done by GPU-accelerated model servers deployed as Docker containers (specifically optimized for AMD GPUs / ROCm).

## Architecture

### Database

A SQLite database (`cache/test.db`, see [helpers/database.py](helpers/database.py)) keeps track of:

- **`contents`**: every video/audio content sniffed (platform, external id, URL, title, duration, uploader, channel id, series, upload date). Only the platform, external id and URL are mandatory; metadata an extractor does not give is `NULL` (except the title: yt-dlp substitutes a generic one).
- **`files`**: files produced for each content (`ytdlp_info`, `ytdlp_audio`, `ytdlp_subtitles`, `16k_mono_wav`, `whisper_result`, ...), stored under `cache/`.
- **`diarizations`**, **`turns`**, **`turn_embeddings`**: diarization runs of a content, their speaker turns, and one voice embedding per turn and model.

All pipeline steps are idempotent: if a file is already registered in the database, it is not re-downloaded / re-computed.

The schema is a list of migrations in [helpers/database.py](helpers/database.py) (`MIGRATIONS`), applied automatically when a script connects. Before migrating an existing database, a copy is saved in `cache/backups/`. Stop running scripts before pulling a change that adds a migration. Unlike the rest of `cache/`, `cache/test.db` and `cache/backups/` cannot be recomputed: keep them when cleaning the cache.

To restore a backup, stop every script, job and datasette, then:
```bash
python restore_backup.py                               # lists the backups
python restore_backup.py cache/backups/<backup>.db
```
It refuses a missing or damaged backup, saves the current database in `cache/backups/` first, and copies with SQLite's backup API (a plain `cp` over a database that still has a `test.db-wal` file can corrupt it).

### Model servers (Docker, AMD/ROCm)

Each model runs as an HTTP server inside a Docker container built and managed by the `run_*_servers.py` scripts (images are built on first run):

| Server | Script | Model | Default port(s) |
|--------|--------|-------|-----------------|
| Whisper | [run_whisper_servers.py](run_whisper_servers.py) | [whisper.cpp](https://github.com/ggml-org/whisper.cpp) `large-v3` + Silero VAD, HIP build | 8070, 8071 |
| Pyannote | [run_pyannote_servers.py](run_pyannote_servers.py) | `pyannote/speaker-diarization-community-1` | 8050 |
| wtpsplit | [run_wtpsplit_servers.py](run_wtpsplit_servers.py) | SaT `sat-12l-sm` (sentence segmentation) | 8060 |
| SpeechBrain | [run_speechbrain_servers.py](run_speechbrain_servers.py) | `speechbrain/spkrec-ecapa-voxceleb` (ECAPA-TDNN, 192-dim voice embeddings) | 8040 |

Python clients for these servers live in [helpers/](helpers/) (`whisper_client.py`, `pyannote_client.py`, `wtpsplit_client.py`, `speechbrain_client.py`). All servers expose a `/health` endpoint; inference endpoints are `/inference` (Whisper), `/diarize` (Pyannote), `/split` (wtpsplit) and `/embed` (SpeechBrain).

The Pyannote server returns both the regular diarization (with overlapping speech) and the exclusive one (at most one speaker at a time). The SpeechBrain server takes a 16 kHz mono wav and a list of `[start, end]` ranges (e.g. the exclusive diarization turns) and returns one L2-normalised embedding per range, or `null` when the range is too short (`min_duration`, 1 s by default). Ranges longer than `max_duration` (30 s by default) are embedded in chunks whose embeddings are averaged:

```bash
curl -F file=@cache/16k_mono_wav/<content>.wav \
     -F 'params={"segments": [[0.0, 5.0], [12.3, 15.9]]}' \
     http://127.0.0.1:8040/embed
```

## Installation

### Prerequisites

1. **Docker Desktop**: [https://docs.docker.com/desktop/](https://docs.docker.com/desktop/)

2. **FFmpeg** (used by yt-dlp for audio extraction and for the 16 kHz mono wav conversion):
   ```bash
   sudo apt install ffmpeg
   ```

3. **Python Dependencies**:
   ```bash
   pip install sqlite-vec yt-dlp docker requests
   ```

4. **JS runtime for yt-dlp**:

   [https://docs.deno.com/runtime/getting_started/installation/](https://docs.deno.com/runtime/getting_started/installation/)
   [https://github.com/yt-dlp/yt-dlp/wiki/EJS#option-1-install-the-yt-dlp-ejs-python-package](https://github.com/yt-dlp/yt-dlp/wiki/EJS#option-1-install-the-yt-dlp-ejs-python-package)

5. **Hugging Face token** (for Pyannote only): accept the conditions of [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1), then put your token in a `hf.token` file at the root of the project. It is only used by a throwaway stage of the image build to download the model: it never appears in the build logs, and neither the final image nor the containers contain it (they load the model offline).

## Usage

1. **Create the database** (optional: every script creates or migrates it when it connects):
   ```bash
   python create_sqlite_db.py
   ```

2. **Start the model servers** you need. In each `run_*_servers.py`, adjust `gpu_instances` (a `{device_id: [ports]}` mapping) and the build parameters (`gfx_version` for Whisper, `sat_model` for wtpsplit) to your hardware, then:
   ```bash
   python run_whisper_servers.py    # transcription
   python run_pyannote_servers.py   # speaker diarization
   python run_wtpsplit_servers.py   # sentence segmentation
   python run_speechbrain_servers.py # voice embeddings
   ```
   Each script builds the Docker image if needed, starts the containers, and streams their logs until you hit Ctrl+C.

3. **Set the server addresses** in [servers.py](servers.py) (one `host:port` per container), and check them:
   ```bash
   python check_servers.py              # OK / BUSY / LOADING / DOWN for each server
   ```

4. **Run a job**: copy [jobs/job_template.py](jobs/job_template.py) to `jobs/<name>.py`, edit its settings (channels or playlists to list, single URLs, upload dates, steps), then:
   ```bash
   python jobs/<name>.py
   ```
   With `DRY_RUN = True` (the template's default) it only lists the selected contents and what is already done for them. With `DRY_RUN = False` it downloads their audio (mp3, then a 16 kHz mono wav), and transcribes and/or diarizes them: each step is spread over all its servers in parallel while the next audios download. Every step is skipped when its result is already in the database, so re-running a job resumes it. The job ends with the list of failures (exit code 1) and one line per content showing what is stored for it.

   For example, "fetch, transcribe and diarize the videos of @name uploaded in March 2026":
   ```python
   LISTING_URLS = ["https://www.youtube.com/@name/videos"]
   UPLOADED_FROM = "2026-03-01"
   UPLOADED_TO = "2026-03-31"
   STEPS = ["download", "transcribe", "diarize"]
   ```

5. **Or run a script** on what is already in the database:

   - [transcript_pending_audios.py](transcript_pending_audios.py) transcribes every content with an audio file (`16k_mono_wav` or `ytdlp_audio`) and no `whisper_result`, e.g. after an interrupted job or failed transcriptions. Contents without audio (e.g. subtitles only) are left out.
   - [diarize_contents.py](diarize_contents.py) diarizes every content with an audio file and no diarization yet for the server's model and the script's `params` (e.g. `{"num_speakers": 2}`). Each run is stored as a `diarizations` row with all its `turns` (regular and exclusive, with the overlapped speech of each turn), and the server's raw answer is kept in `cache/pyannote_result/`.
   - [sniff_and_transcript_channel_audios.py](sniff_and_transcript_channel_audios.py) downloads and transcribes every video of a channel (a job without dates).
   - [sniff_channel_subtitles.py](sniff_channel_subtitles.py) downloads a channel's existing subtitles.

6. **Explore the database**:
   ```bash
   python3 -m datasette cache/test.db --port 8001
   ```

## AI agents

[AGENTS.md](AGENTS.md) (imported by [CLAUDE.md](CLAUDE.md)) describes the repository and its rules for AI coding agents, and [.claude/skills/](.claude/skills/) holds step-by-step procedures written so that a small model can follow them:

| Skill | For requests like |
|---|---|
| [pipeline-job](.claude/skills/pipeline-job/SKILL.md) | "fetch, transcribe and diarize all videos from channel X uploaded between Y and Z": writes a job from the template, dry-runs it, runs it in the background and reports |
| [query-database](.claude/skills/query-database/SKILL.md) | "which videos of X are not diarized yet?", "how long does each speaker talk in video N?" (read-only SQL) |
| [model-servers](.claude/skills/model-servers/SKILL.md) | checking, starting or stopping the model servers |
| [write-pipeline-script](.claude/skills/write-pipeline-script/SKILL.md) | anything the job template cannot do: a custom selection, a new step, an export |

- Claude Code loads `.claude/skills/` and `CLAUDE.md`. Agents that follow the open Agent Skills convention (Codex, Gemini CLI, Cursor, Copilot, OpenCode, goose...) find the same skills through the `.agents/skills` link, and `GEMINI.md` imports `AGENTS.md` for Gemini CLI. Any other agent can simply be told to read `AGENTS.md` first.
- In Claude Code, [.claude/hooks/protect_data.py](.claude/hooks/protect_data.py) (registered in [.claude/settings.json](.claude/settings.json)) blocks the usual ways an agent could destroy `cache/test.db` or `cache/backups/`: deleting, moving or copying over them, `git clean` with `-x`, SQL writes through the `sqlite3` command line.
- The skills were tested with a small model on sandbox copies of the repository, with fake servers and fake channels: requests in English and French, relative dates ("la semaine dernière"), re-diarization with a speaker count, a server down, an ambiguous date (the agent asks), a failing content (retried once, then reported), single URLs, questions on the database and an export script. A local model (e.g. through `ollama launch`) needs a context of at least 64k tokens to hold a skill and a job's output.

## TODO

See [PLAN.md](PLAN.md) for the full review and the phased improvement plan (bug fixes, DB migrations, SpeechBrain embedding server, speaker identification, new sources).

1. Find a way to retrieve (or scrap) videos list of france.tv etc...
2. Integrate the wtpsplit sentence segmentation into the sniffing pipeline
3. Integrate the SpeechBrain embedding server into the pipeline: store turn embeddings in the database and identify speakers

### Potential medias

### Television
- `bfmtv`
- `bfmtv:article`
- `bfmtv:live`
- `LCI`
- `LCP (currently broken)`
- `franceinfo`
- `francetv`
- `TF1`
- `TV5MONDE`

### Press & Digital Media
- `Lemonde`
- `LeFigaroVideoEmbed`
- `LeFigaroVideoSection`
- `20min: (Currently broken)`

### Public Radio
- `RadioFranceLive`
- `RadioFrancePodcast`
- `RadioFranceProfile`
- `RadioFranceProgramSchedule`
*(Covers France Info, RFI, France Inter, etc.)*
