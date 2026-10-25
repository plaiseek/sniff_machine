# Sniff Machine

A pipeline for automatically downloading and transcribing videos from YouTube, Twitch (and, soon, many other platforms). Everything is tracked in a SQLite database, and the heavy lifting (transcription, diarization, sentence segmentation) is done by GPU-accelerated model servers deployed as Docker containers (specifically optimized for AMD GPUs / ROCm).

## Architecture

### Database

A SQLite database (`cache/test.db`, see [helpers/database.py](helpers/database.py)) keeps track of:

- **`contents`**: every video/audio content sniffed (platform, external id, URL, title, duration, uploader, channel id, series, upload date). Only the platform, external id and URL are mandatory; metadata an extractor does not give is `NULL` (except the title: yt-dlp substitutes a generic one).
- **`files`**: files produced for each content (`ytdlp_info`, `ytdlp_audio`, `ytdlp_subtitles`, `16k_mono_wav`, `whisper_result`, ...), stored under `cache/`.
- **`diarizations`**, **`turns`**, **`turn_embeddings`**: diarization runs of a content, their speaker turns, and one voice embedding per turn and model.
- **`speakers`**, **`speaker_samples`** (+ **`sample_embeddings`**), **`speaker_labels`**: named people, reference ranges of their voice, and who each diarization label is (set by a person, or by `identify_speakers.py` with its similarity). Deleting a speaker deletes everything derived from their voice.

All pipeline steps are idempotent: if a file is already registered in the database, it is not re-downloaded / re-computed.

The schema is a list of migrations in [helpers/database.py](helpers/database.py) (`MIGRATIONS`), applied automatically when a script connects. Before migrating an existing database, a copy is saved in `cache/backups/`. Stop running scripts before pulling a change that adds a migration. Unlike the rest of `cache/`, `cache/test.db` and `cache/backups/` cannot be recomputed: keep them when cleaning the cache.

To restore a backup, stop every script, job and datasette, then:
```bash
python3.11 restore_backup.py                               # lists the backups
python3.11 restore_backup.py cache/backups/<backup>.db
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
   python3.11 -m pip install sqlite-vec yt-dlp docker requests numpy
   ```

4. **JS runtime for yt-dlp**:

   [https://docs.deno.com/runtime/getting_started/installation/](https://docs.deno.com/runtime/getting_started/installation/)
   [https://github.com/yt-dlp/yt-dlp/wiki/EJS#option-1-install-the-yt-dlp-ejs-python-package](https://github.com/yt-dlp/yt-dlp/wiki/EJS#option-1-install-the-yt-dlp-ejs-python-package)

5. **Hugging Face token** (for Pyannote only): accept the conditions of [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1), then put your token in a `hf.token` file at the root of the project. It is only used by a throwaway stage of the image build to download the model: it never appears in the build logs, and neither the final image nor the containers contain it (they load the model offline).

## Usage

1. **Create the database** (optional: every script creates or migrates it when it connects):
   ```bash
   python3.11 create_sqlite_db.py
   ```

2. **Start the model servers** you need. In each `run_*_servers.py`, adjust `gpu_instances` (a `{device_id: [ports]}` mapping) and the build parameters (`gfx_version` for Whisper, `sat_model` for wtpsplit) to your hardware, then:
   ```bash
   python3.11 run_whisper_servers.py    # transcription
   python3.11 run_pyannote_servers.py   # speaker diarization
   python3.11 run_wtpsplit_servers.py   # sentence segmentation
   python3.11 run_speechbrain_servers.py # voice embeddings
   ```
   Each script builds the Docker image if needed, starts the containers, and streams their logs until you hit Ctrl+C.

3. **Set the server addresses** in [servers.py](servers.py) (one `host:port` per container), and check them:
   ```bash
   python3.11 check_servers.py              # OK / BUSY / LOADING / DOWN for each server
   ```

4. **Run a job**: copy [jobs/job_template.py](jobs/job_template.py) to `jobs/<name>.py`, edit its settings (channels or playlists to list, single URLs, upload dates, steps), then:
   ```bash
   python3.11 jobs/<name>.py
   ```
   With `DRY_RUN = True` (the template's default) it only lists the selected contents and what is already done for them. With `DRY_RUN = False` it downloads their audio (mp3, then a 16 kHz mono wav), transcribes and/or diarizes them, and with `"embed"` in `STEPS` computes the voice embeddings of the diarized turns once the diarizations are done: each step is spread over all its servers in parallel while the next audios download. Every step is skipped when its result is already in the database, so re-running a job resumes it. The job ends with the list of failures (exit code 1) and one line per content showing what is stored for it.

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
   - [embed_speaker_turns.py](embed_speaker_turns.py) computes the voice embedding (SpeechBrain) of every exclusive turn of every diarization that has none yet for the server's model, one request per content. Turns shorter than 1 s get a `NULL` embedding, so that they are not sent again.
   - [sniff_and_transcript_channel_audios.py](sniff_and_transcript_channel_audios.py) downloads and transcribes every video of a channel (a job without dates).
   - [sniff_channel_subtitles.py](sniff_channel_subtitles.py) downloads a channel's existing subtitles.

6. **Name the speakers** (after `embed_speaker_turns.py` or a job with `"embed"`):
   ```bash
   python3.11 listen_speaker.py 243                          # the labels of content 243: minutes, clean minutes, who they are
   python3.11 listen_speaker.py 243 SPEAKER_01               # a clip of the label's longest clean turns + what is said + links
   python3.11 enroll_speaker.py "Name" 243 --label SPEAKER_01  # this label is Name (or START END ranges instead of --label)
   python3.11 identify_speakers.py --dry-run                 # which labels of the other contents are Name, then without --dry-run
   python3.11 evaluate_speakers.py                           # precision / recall per threshold, from the labels you set
   ```
   A label's voice is the duration-weighted mean of the embeddings of its clean turns (at least 3 s, at most 10 % overlapped speech). `identify_speakers.py` names a label after the closest enrolled speaker when their cosine similarity reaches `--threshold` (0.6 by default, provisional: on the first five videos, one voice in different videos scored 0.90-0.95, different voices at most 0.45), and never changes a label you set. `enroll_speaker.py --forget "Name"` deletes a speaker and all that names them (older copies remain in `cache/backups/`).

   Voiceprints that identify named people are biometric data (GDPR art. 9): enroll public figures in their public role only, keep the database, the audio and `cache/speaker_clips/` private, and check automatic names before publishing anything built on them.

7. **Explore the database**:
   ```bash
   python3.11 -m datasette cache/test.db --port 8001
   ```

## AI agents

[AGENTS.md](AGENTS.md) (imported by [CLAUDE.md](CLAUDE.md)) describes the repository and its rules for AI coding agents, and [.claude/skills/](.claude/skills/) holds step-by-step procedures written so that a small model can follow them:

| Skill | For requests like |
|---|---|
| [pipeline-job](.claude/skills/pipeline-job/SKILL.md) | "fetch, transcribe and diarize all videos from channel X uploaded between Y and Z": writes a job from the template, dry-runs it, runs it in the background and reports |
| [query-database](.claude/skills/query-database/SKILL.md) | "which videos of X are not diarized yet?", "how long does each speaker talk in video N?" (read-only SQL) |
| [speaker-identity](.claude/skills/speaker-identity/SKILL.md) | "who is SPEAKER_01 in video 243?", "SPEAKER_00 of video 418 is Jean Dupont, find him in the other videos" (the agent never names anyone itself) |
| [model-servers](.claude/skills/model-servers/SKILL.md) | checking, starting or stopping the model servers |
| [write-pipeline-script](.claude/skills/write-pipeline-script/SKILL.md) | anything the job template cannot do: a custom selection, a new step, an export |

- Claude Code loads `.claude/skills/` and `CLAUDE.md`. Agents that follow the open Agent Skills convention (Codex, Gemini CLI, Cursor, Copilot, OpenCode, goose...) find the same skills through the `.agents/skills` link, and `GEMINI.md` imports `AGENTS.md` for Gemini CLI. Any other agent can simply be told to read `AGENTS.md` first.
- In Claude Code, [.claude/hooks/protect_data.py](.claude/hooks/protect_data.py) (registered in [.claude/settings.json](.claude/settings.json)) blocks the usual ways an agent could destroy `cache/test.db` or `cache/backups/`: deleting, moving or copying over them (also from inside `cache/`), `git clean` with `-x`, any use of the `sqlite3` command line on the database without `-readonly`, and `enroll_speaker.py --forget`.
- The skills were tested with a small model on sandbox copies of the repository, with fake servers and fake channels: requests in English and French, relative dates ("la semaine dernière"), re-diarization with a speaker count, a server down, an ambiguous date (the agent asks), a failing content (retried once, then reported), single URLs, questions on the database and an export script. A local model (e.g. through `ollama launch`) needs a context of at least 64k tokens to hold a skill and a job's output.

## TODO

See [PLAN.md](PLAN.md) for the full review and the phased improvement plan (bug fixes, DB migrations, SpeechBrain embedding server, speaker identification, new sources).

1. Find a way to retrieve (or scrap) videos list of france.tv etc...
2. Integrate the wtpsplit sentence segmentation into the sniffing pipeline

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
