# Sniff Machine

A pipeline for automatically downloading and transcribing videos from YouTube, Twitch (and, soon, many other platforms). Everything is tracked in a SQLite database, and the heavy lifting (transcription, diarization, sentence segmentation) is done by GPU-accelerated model servers deployed as Docker containers (specifically optimized for AMD GPUs / ROCm).

## Architecture

### Database

A SQLite database (`cache/test.db`, see [helpers/database.py](helpers/database.py): `connect()`, and the queries of [helpers/tables/](helpers/tables/)) keeps track of:

- **`contents`**: every video/audio content sniffed (platform, external id, URL, title, duration, uploader, channel id, series, upload date). Only the platform, external id and URL are mandatory; metadata an extractor does not give is `NULL` (except the title: yt-dlp substitutes a generic one).
- **`files`**: files produced for each content (`ytdlp_info`, `ytdlp_audio`, `ytdlp_subtitles`, `16k_mono_wav`, `whisper_result`, ...), stored under `cache/`.
- **`diarizations`**, **`turns`**, **`turn_embeddings`**: diarization runs of a content, their speaker turns, and one voice embedding per turn and model.
- **`speakers`**, **`speaker_samples`** (+ **`sample_embeddings`**), **`speaker_labels`**: named people, reference ranges of their voice, and who each diarization label is (set by a person, or by `identify_speakers.py` with its similarity). Deleting a speaker deletes everything derived from their voice.
- **`transcriptions`**, **`words`**, **`sentences`** (+ the full-text index **`sentences_fts`**): the words of each transcription (text, normalised form, time, probability) with the speaker turn each one falls in (the content's latest diarization), and their sentences, never across two speakers.
- **`questions`**, **`question_contents`**, **`findings`**: questions asked to an LLM about the transcriptions (`ask_transcripts.py`), the contents each one was asked on, and the passages it checked, with its verdict, confidence, quote and explanation.

All pipeline steps are idempotent: if a file is already registered in the database, it is not re-downloaded / re-computed.

The schema is a list of migrations in [helpers/migrations.py](helpers/migrations.py) (`MIGRATIONS`), applied automatically when a script connects. Before migrating an existing database, a copy is saved in `cache/backups/`. Stop running scripts before pulling a change that adds a migration. Unlike the rest of `cache/`, `cache/test.db` and `cache/backups/` cannot be recomputed: keep them when cleaning the cache.

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

`ask_transcripts.py` also needs an LLM served by [vLLM](https://docs.vllm.ai/) (OpenAI-compatible API, default port 8000), which this repository does not build: start it wherever it runs, with the reasoning parser of the model (`qwen3` for Qwen3, see vLLM's list), e.g. `vllm serve <model> --max-model-len 262144 --reasoning-parser qwen3`, and list it in `VLLM_SERVERS` of [servers.py](servers.py).

Python clients for these servers live in [helpers/](helpers/) (`whisper_client.py`, `pyannote_client.py`, `wtpsplit_client.py`, `speechbrain_client.py`, `vllm_client.py`). All servers expose a `/health` endpoint; inference endpoints are `/inference` (Whisper), `/diarize` (Pyannote), `/split` (wtpsplit) and `/embed` (SpeechBrain).

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
   python3.11 -m pip install -r requirements.txt
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
   With `DRY_RUN = True` (the template's default) it only lists the selected contents and what is already done for them. With `DRY_RUN = False` it downloads their audio (mp3, then a 16 kHz mono wav), transcribes and/or diarizes them (storing the words of the transcriptions in the database, each with its speaker turn), with `"embed"` in `STEPS` computes the voice embeddings of the diarized turns once the diarizations are done, and with `"split"` cuts the transcriptions into sentences: each step is spread over all its servers in parallel while the next audios download. Every step is skipped when its result is already in the database, so re-running a job resumes it. The job ends with the list of failures (exit code 1) and one line per content showing what is stored for it.

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
   - [store_words.py](store_words.py) stores the words of every transcription not stored yet, and aligns them to the diarizations made since (no server needed). The transcription and diarization scripts and the jobs do it too.
   - [split_sentences.py](split_sentences.py) cuts the stored words of each content into runs of one speaker, then into sentences with wtpsplit (`--punctuation`: after `. ? ! …`, no server).
   - [embed_speaker_turns.py](embed_speaker_turns.py) computes the voice embedding (SpeechBrain) of every exclusive turn of every diarization that has none yet for the server's model, one request per content. Turns shorter than 1 s get a `NULL` embedding, so that they are not sent again.

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

7. **Read what was said, and by whom** (after `store_words.py`, or a job that transcribes):
   ```bash
   python3.11 show_transcript.py 243 --from 10:00 --to 15:00     # paragraphs per speaker, with times, links and names when known
   python3.11 search_transcripts.py '"mairie de paris"'          # who said it, where (needs split_sentences.py); pete* = péter, pété...
   python3.11 plot_word_counts.py Psyhodelik --speaker "Name" --output counts.png   # cumulated word counts (edit THEMES)
   ```
   The words have the times of Whisper's tokens, and the speaker of the exclusive turn their middle falls in (about 5 % fall between turns). `plot_word_counts.py` needs matplotlib (`python3.11 -m pip install matplotlib`; on Ubuntu, python3.11 may also need `pip install --user pillow`).

8. **Ask an LLM where something is said** (a vLLM server, see above), when no fixed words find it:
   ```bash
   python3.11 ask_transcripts.py "Où parle-t-on de la mairie de Paris ?" --uploader Psyhodelik --dry-run   # how much it reads, how long it takes
   nohup python3.11 ask_transcripts.py "Quels propos pourraient être poursuivis comme discours de haine selon la loi du 29 juillet 1881 ?" \
       --criteria criteria/haine_loi_1881.txt --uploader Psyhodelik --uploaded-from 2025-10-26 > ask.log 2>&1 &   # the last year (see below)
   python3.11 ask_transcripts.py "Quels propos ..." --criteria criteria/haine_loi_1881.txt --uploader Psyhodelik --uploaded-from 2025-10-26 --show
   ```
   It reads each transcription in windows of about 5,000 words (30 min) with times and speakers, and lists the passages that may match (first pass, no thinking), then checks each one with 600 words around it (second pass, the model thinks first): the matching sentences and a quote, what the speaker does with these words (own words, approving what they quote or read, quoting or reading without a stance, quoting to criticize), what of the question it meets (`Meets:`), what speaks against it (`Against:`), why, the verdict (`yes`, `unsure`, `no`) and a confidence. [criteria/haine_loi_1881.txt](criteria/haine_loi_1881.txt) defines hateful speech under French press law, to find leads for a lawyer: `Meets:` gives the offence, the group targeted and the protected criterion, `Against:` what a defence could object. These offences are prescribed one year after publication (to be confirmed by the lawyer), hence `--uploaded-from` a year back. The criteria are part of the question: editing them asks everything again, so have them checked before a long run. Each vLLM server gets `--clients` requests at once (default 8, where it is most efficient), never needing more than `--kv-tokens` of KV cache (default 440,000), the contents that started first going first; `--tokens-per-s` (default 300) gives the dry run's time estimate, and each content's line gives the speed measured and the time left. The LLM cites sentence numbers, never times: they are mapped back to the words, with a link to each moment. `--criteria FILE` adds a precise definition of what matches to the question; `--speaker` keeps the sentences of the labels named so. Asked again, it only reads the contents not read yet (or diarized since); a new question, criteria, speaker, model or prompt version reads everything again. The whole Psyhodelik channel (1.47 M words) is about 2.9 M tokens read by the first pass, and 2.6 to 10.5 M tokens written by both passes (the hateful-speech criteria are the high end, about 7 tokens per word: many passages to check, with thinking): about 2.5 to 10 h at 300 tokens/s, plus the reading. The answers are leads to listen to, not verdicts.

9. **Explore the database**:
   ```bash
   python3.11 -m datasette cache/test.db --port 8001
   ```

## AI agents

[AGENTS.md](AGENTS.md) (imported by [CLAUDE.md](CLAUDE.md)) describes the repository and its rules for AI coding agents, and [.claude/skills/](.claude/skills/) holds step-by-step procedures written so that a small model can follow them:

| Skill | For requests like |
|---|---|
| [pipeline-job](.claude/skills/pipeline-job/SKILL.md) | "fetch, transcribe and diarize all videos from channel X uploaded between Y and Z": writes a job from the template, dry-runs it, runs it in the background and reports |
| [query-database](.claude/skills/query-database/SKILL.md) | "which videos of X are not diarized yet?", "how long does each speaker talk in video N?", "who said 'mairie de Paris'?", "how many times does X say Y?" (read-only SQL) |
| [ask-transcripts](.claude/skills/ask-transcripts/SKILL.md) | "when does X say something hateful about a group?", "find the passages that could be prosecuted", "where does he talk about the police?": asks the LLM with a dry run and the user's go-ahead first, then reports the leads |
| [speaker-identity](.claude/skills/speaker-identity/SKILL.md) | "who is SPEAKER_01 in video 243?", "SPEAKER_00 of video 418 is Jean Dupont, find him in the other videos" (the agent never names anyone itself) |
| [model-servers](.claude/skills/model-servers/SKILL.md) | checking, starting or stopping the model servers |
| [write-pipeline-script](.claude/skills/write-pipeline-script/SKILL.md) | anything the job template cannot do: a custom selection, a new step, an export |

- Claude Code loads `.claude/skills/` and `CLAUDE.md`. Agents that follow the open Agent Skills convention (Codex, Gemini CLI, Cursor, Copilot, OpenCode, goose...) find the same skills through the `.agents/skills` link, and `GEMINI.md` imports `AGENTS.md` for Gemini CLI. Any other agent can simply be told to read `AGENTS.md` first.
- In Claude Code, [.claude/hooks/protect_data.py](.claude/hooks/protect_data.py) (registered in [.claude/settings.json](.claude/settings.json)) blocks the usual ways an agent could destroy `cache/test.db` or `cache/backups/`: deleting, moving or copying over them (also from inside `cache/`), `git clean` with `-x`, any use of the `sqlite3` command line on the database without `-readonly`, and `enroll_speaker.py --forget`.
- The skills were tested with a small model on sandbox copies of the repository, with fake servers and fake channels: requests in English and French, relative dates ("la semaine dernière"), re-diarization with a speaker count, a server down, an ambiguous date (the agent asks), a failing content (retried once, then reported), single URLs, questions on the database and an export script. A local model (e.g. through `ollama launch`) needs a context of at least 64k tokens to hold a skill and a job's output.

## Tests

```bash
python3.11 -m unittest discover tests
```

No GPU, network or Docker needed: the migrations on a database of their own, and `ask_transcripts.py`'s question step against a fake vLLM server ([tests/fake_vllm.py](tests/fake_vllm.py)): requests at once, the KV-cache limit, a window cut then read in halves, an empty answer asked again, an error that cancels the rest, resuming.

## Roadmap

[PLAN.md](PLAN.md) lists what is still to do (and links the October 2026 review, with the design of every phase), and [docs/sources.md](docs/sources.md) the French media to add.
