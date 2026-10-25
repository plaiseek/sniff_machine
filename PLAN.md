# Sniff Machine — review and improvement plan

Reviewed at commit `3dbf8ee`. Every finding below was checked against the code (file:line references are to that commit). External facts (whisper.cpp, docker-py, pyannote.audio 4.x, SpeechBrain, sqlite-vec, yt-dlp extractors) were checked against their current sources where possible. Nothing was run on a GPU, so ROCm runtime behaviour is unverified and marked as such.

**Status:** Phase 1, the Phase 2 server and the Phase 3 migrations are implemented on this branch (see the implementation notes of each phase). Everything else is still to do.

**Objectives, as I understand them:** download audio/video from YouTube, Twitch and French TV/radio/press, then transcribe → diarize → embed voices → identify speakers → analyse ("who said what", word counts). Everything is tracked in **one central SQLite database**, with GPU work done by **small, self-contained model servers** (one file per server, one client, one launcher, one script per feature).

The plan keeps those two values. It adds no framework, ORM or orchestrator. The only shared code it proposes is a migration list in `helpers/database.py` and a few small helpers.

---

## TL;DR

1. **The transcription pipeline cannot run end-to-end today.** `assert_whisper_servers` probes `/ready`, which whisper.cpp doesn't serve, and then exits with status 0. `cache/16k_mono_wav/` is never created, so ffmpeg fails for every video. The wav is built, but the mp3 is what gets sent to Whisper.
2. **Docker build failures are silently ignored** (`helpers/docker.py`). A broken build starts the *previous* image. Fix this before building any new server, including SpeechBrain.
3. **Diarization of long VODs gets killed after 120 s** (gunicorn sync worker `--timeout 120`), and the client forces `num_speakers=2`.
4. **The schema cannot evolve.** `files.file_type` has a `CHECK` list, and `CREATE TABLE IF NOT EXISTS` never changes an existing DB. Adding diarization turns or voice embeddings therefore needs a migration mechanism first (`PRAGMA user_version`, about 30 lines).
5. **The SpeechBrain server can be built as soon as (2) is fixed.** It is stateless (audio + time ranges in, one vector per range out), so you can test it with `curl` before any DB work. Its full design is in [Phase 2](#phase-2--speechbrain-voice-embedding-server-the-requested-feature).

**Recommended order:**

```
Phase 0 (protect)  →  Phase 1 (fix pipeline)  →  Phase 2 (SpeechBrain server, standalone)
                                              →  Phase 3 (DB migrations)  →  Phase 4 (diarization → DB)
                                                                          →  Phase 5 (embeddings → DB, speaker identity)
                                                                          →  Phase 6 (transcripts → DB, who said what, wtpsplit)
Phase 7 (sources & scheduling) and Phase 8 (hygiene) can be done in parallel once Phase 3 has landed.
```

Effort: **S** < 1 h · **M** ≈ half a day to a day · **L** several days.

---

## What is good and should stay

- **One SQLite DB as the single source of truth**, with idempotent "already in DB → skip" steps.
- **One self-contained module per model server** (Dockerfile + server code + build/run/stop functions), a matching `*_client.py` and a tiny `run_*_servers.py`. It is easy to add a server by copying one.
- **Stateless HTTP model servers** with strict `accepted_params` validation. Clients get clear 400s.
- **sqlite-vec is already loaded** (`helpers/database.py:13-15`). That's the right tool for voice embeddings.
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
| 0.1 | Move the DB out of the disposable cache: `cache/test.db` → `data/sniff.db` (`helpers/database.py:7`). Run `PRAGMA wal_checkpoint(TRUNCATE)` first, or copy with `VACUUM INTO`. | `rm -rf cache` to free disk would delete the only non-recomputable data: speaker names, enrollments, human corrections. |
| 0.2 | Add a ~10-line `backup_db.py`: `VACUUM INTO 'backups/sniff-<timestamp>.db'` (safe while other connections are open). Call it before every migration. | The Phase 3 migrations rebuild tables. |
| 0.3 | Record the **working** image configuration: `docker image inspect rocm/pytorch:latest --format '{{index .RepoDigests 0}}'`; `docker run --rm --entrypoint pip amd_pyannote:latest freeze > helpers/constraints_pyannote.txt` (same for `amd_wtpsplit`); `docker run --rm --entrypoint git amd_whisper:latest -C /app rev-parse HEAD`. | docker-py does not re-pull `latest` when a local copy exists, so your working pyannote image was built from a *cached* base. `latest` on Docker Hub has moved on, and `torchcodec v0.10.0` (`pyannote_amd_server.py:7`) only matches the torch it was built against. A fresh machine or a `docker system prune` will break the build, and the digest you have now cannot be recovered afterwards. |

## Phase 1 — Make the existing pipeline run (all small, mostly S)

| # | Sev. | Problem | Where | Fix |
|---|---|---|---|---|
| 1.1 | high | Whisper readiness probe hits `/ready`. whisper.cpp only serves `/`, `/inference`, `/load` and `/health` (checked on master), so the request gets a 404, then `sys.exit()` with **status 0**. It also runs *after* listing the whole channel. | `helpers/whisper_client.py:11-20`, `sniff_and_transcript_channel_audios.py:11,20`, README:26 | Probe `GET /health` and require `{"status": "ok"}` (503 = still loading). Exit with `sys.exit("…not ready")` (status 1). Check servers before listing. Rename `/ready` → `/health` in the pyannote/wtpsplit servers too, so all servers share one contract. |
| 1.2 | high | `cache/16k_mono_wav/` is never created, and ffmpeg doesn't create directories, so every conversion fails. `verbose=False` discards stderr, so the reason is lost. The error is logged as "Failed to download audio". | `helpers/ffmpeg.py:5-48`, `sniff_and_transcript_channel_audios.py:77-83` | `output_path.parent.mkdir(parents=True, exist_ok=True)` in `convert_to_16k_mono_wav`. Keep the last stderr lines and include them in the `RuntimeError`. |
| 1.3 | medium | The wav is built and registered, but the **mp3** is queued for Whisper. The log line says "Transcripting {wav_path}", and the client sends it as `audio/mpeg`. | `sniff_and_transcript_channel_audios.py:85`, `helpers/whisper_client.py:63-95` | Queue `wav_path`. Rename `mp3_to_whisper_result` → `wav_to_whisper_result` and send it as `audio/wav`. The same file then goes to Whisper, pyannote and SpeechBrain (convention 3). |
| 1.4 | high | `client.api.build(..., decode=True)` (the low-level API) **never raises `BuildError`**. Failures arrive as `{"error": …}` chunks, which are skipped, and the previous image is started. | `helpers/docker.py:12-32` | For each chunk: `if "error" in entry: sys.exit(f"Build failed: {entry['error']}")`. Print `entry.get("stream", "").rstrip()` only when non-empty (`line[-1]` raises on an empty string). Pass `rm=True, forcerm=True`. Remove the dead `except BuildError`. **Do this before building the SpeechBrain image.** |
| 1.5 | high | gunicorn with `-w 1 --threads 1` is a *sync* worker, so `--timeout 120` kills any request longer than 2 min, e.g. diarizing a multi-hour VOD. The client then gets a ConnectionError. | `helpers/pyannote_amd_server.py:120`, `helpers/wtpsplit_amd_server.py:75` | `--timeout 0`. Add `ENV PYTHONUNBUFFERED=1` so logs aren't buffered. |
| 1.6 | medium | The pyannote client forces `num_speakers=2`. News broadcasts have 20+ speakers, and Twitch has the streamer plus game/TTS voices. Wrong turns would poison every embedding. | `helpers/pyannote_client.py:21` | `params: dict \| None = None`. Use `min_speakers`/`max_speakers` per source; default `{}` lets pyannote estimate. |
| 1.7 | medium | The pyannote server saves uploads to `uploads/<name>` and never deletes them. Same-named uploads overwrite each other, and `secure_filename` can return `""`. | `helpers/pyannote_amd_server.py:81-84` | `tempfile.NamedTemporaryFile(suffix=".wav")` plus deletion in `finally`, or read from the stream (see 4.1). |
| 1.8 | low | A trailing newline in `hf.token` (what `echo hf_x > hf.token` and most editors produce) breaks both the generated Dockerfile and the server script. | `helpers/pyannote_amd_server.py:13-14` | `f.read().strip()`. Phase 8.4 removes the token from the image entirely. |
| 1.9 | medium | Failures are lost. `failed_urls` and `failed_transcriptions` are never printed. Every `assert_*` exits with status 0. **Ctrl+C during a download hangs forever**: the consumers block on `queue.get()` because the `None` sentinels are only queued after the loop. A dead Whisper container "fails" the whole queue in seconds while the healthy one is busy. | `sniff_and_transcript_channel_audios.py:28-92`, `helpers/*_client.py` | Wrap the producer loop in `try/finally`: drain the queue, then put one `None` per server. On a `ConnectionError`, re-queue the item and stop that consumer. Print the failures and `sys.exit(1)` if there were any. Optionally use `Queue(maxsize=2 * len(servers))`. |
| 1.10 | medium | `add_ytdlp_content` uses `info["uploader"]`, `info["timestamp"]` and `info["duration"]`. France TV has no `uploader`. YouTube sets `timestamp=None` when the upload time is unknown. Live and upcoming items have `duration=None` (NOT NULL violation). A Twitch VOD of a stream still in progress has `is_live=True` and gets cached truncated. | `helpers/yt_dlp.py:25-36` | Use `.get()` with fallbacks: `uploader or channel or series`; `timestamp or release_timestamp`, falling back to `upload_date`. Skip `is_live` and `live_status in ("is_live", "is_upcoming", "post_live")`, and record the skip as a failure so it is visible. The schema side is in Phase 3.2. |
| 1.11 | medium | Dedup is by exact URL, but the insert is `UNIQUE(platform, external_id)`. So `youtu.be/x` after `watch?v=x`, `&t=60`, or a franceinfo article embedding a france.tv video all raise `IntegrityError` on every run. | `helpers/yt_dlp.py:15`, `helpers/database.py:91-99` | After `extract_info`, look up by `(info["extractor_key"], info["id"])` and reuse that row. Keep storing the URL as given: `webpage_url` can be an internal value (France TV gives `francetv:<id>#__youtubedl_smuggle=…`). |
| 1.12 | low | `force=True` always crashes, because `add_content_file` is a plain `INSERT`. Cache hits trust DB rows even when the file was deleted. Only the whisper step checks `is_file()`. | `helpers/database.py:102-112`, `helpers/yt_dlp.py` | Make it an upsert: `ON CONFLICT(content_id, file_type) DO UPDATE SET path = excluded.path`. Add one `get_existing_file()` that also checks `is_file()`, and use it everywhere. |
| 1.13 | low | `load_sparsified_transcription` **removes every newline** when there is nothing to remove: the empty alternation `"()\n"` matches every `\n` (reproduced). Duplicate timestamps remove too many markers. | `helpers/subtitles.py:76-110` | Return early when `number_to_remove <= 0`. Remove markers by index, not by value. |
| 1.14 | low | Subtitles: `lang` and manual vs automatic are not part of the cache key. Platforms without srt (France TV serves vtt/ttml) are reported as "no subtitles" while a `.vtt` file sits unregistered. | `helpers/yt_dlp.py:91-127` | Add the `FFmpegSubtitlesConvertor` postprocessor (`format: srt`, `when: before_dl`). Put the language into the file key (see the `variant` column in 3.2). |
| 1.15 | low | `whisper_result_to_srt` raises IndexError on a segment with no `words` and mishandles `t_dtw == -1`. | `helpers/whisper_client.py:122-132` | Guard both cases. |
| 1.16 | low | The plot regexes `[^a-z]…[^a-z]` consume their separator. `" pipi pipi pipi "` counts 2, accented letters act as word boundaries (`trépied` counts as `pied`), and an empty date filter raises IndexError. | `plot_pipi_caca_panard.py:29-53,127` | Use lookarounds such as `(?<![^\W\d_])(pipis?\|pisser?)(?![^\W\d_])`. Phase 6 moves this script onto the DB. |

**Implementation notes (done).**
- **`rocm/pytorch:latest` moved** (the 0.3 risk materialised): the new image is Ubuntu 24.04 with Debian's Python, so it has no `python` command and pip can't upgrade itself, and its torch no longer matches torchcodec v0.10.0. Changes:
  - all Dockerfiles use `python3` and never upgrade pip;
  - pip is constrained to the base image's torch/torchaudio;
  - the pyannote server decodes the upload with `soundfile` and passes pyannote an in-memory waveform (pyannote.audio 4 supports this without torchcodec), which removes the torchcodec source build and its ffmpeg/libav layer;
  - pyannote.audio is pinned to 4.0.7, and its default telemetry to otel.pyannote.ai is turned off (`PYANNOTE_METRICS_ENABLED=0`).
  - The HF token is out of the pyannote image (part of 8.4), via a multi-stage build:
    - `hf.token` is sent in the build context (`build_image(..., files=...)`) to a throwaway `download` stage, which downloads the model;
    - the build fails if the token turns up in the downloaded cache;
    - the final image only gets `HF_HOME/hub` and loads it with `HF_HUB_OFFLINE=1`, so containers need no token and no network.
    - The `download` stage's cached layers (which hold the token) stay in the local build cache: `docker builder prune` removes them.
  - The "pip as root" and pyannote's torchcodec warnings are silenced (`PIP_ROOT_USER_ACTION=ignore`, and a `PYTHONWARNINGS` filter on `pyannote.audio.core.io` only).
- The transcription script skips already-transcribed contents *before* any download, and only fetches the mp3 when the wav is missing. Deleting `cache/ytdlp_audio` (or the wavs) after a run therefore costs nothing.
- Workers stop when downloads are over and nothing is pending (`Queue.unfinished_tasks`), so an item handed back by a dying server is still picked up.
- Known trade-off of 1.9: a file that *crashes* whisper.cpp is handed to each server in turn and takes them all down. It's reported as "Not transcribed". Record such failures once the Phase 3 `failures` table exists.
- 1.14 covers the language only: one subtitles file per content until the `variant` column of 3.2.
- Also fixed:
  - ffmpeg is killed on Ctrl+C instead of being left running;
  - `noplaylist` is set, so a `watch?v=X&list=Y` URL is video X;
  - a real playlist is rejected after one request;
  - a busy Python server (single sync worker) is reported as busy rather than not ready;
  - the plot counts *pété/pétés/pétée(s)* again.

---

## Phase 2 — SpeechBrain voice-embedding server (the requested feature)

**Prerequisite:** 1.4 (build errors detected). It's also worth doing 0.3 first, so the image shares the pinned base with pyannote. The server is stateless, so it doesn't need Phase 3: test it with `curl` and hand-typed ranges, then wire it to the DB in Phase 5.

### Files (same pattern as the existing servers)

| File | Contents |
|---|---|
| `helpers/speechbrain_amd_server.py` | `build_speechbrain_image()`, `run_speechbrain_container(port, rocm_device)`, `stop_all_speechbrain_containers()`, the Dockerfile and the server script. |
| `helpers/speechbrain_client.py` | `assert_speechbrain_servers(addresses)`; `wav_to_speechbrain_embeddings(address, wav_path, segments, min_duration=1.0, max_duration=30.0, batch_size=16) -> dict`; and a helper that accepts pyannote's `{speaker: [[start, end], …]}` and returns turns sorted by start with their embeddings. |
| `run_speechbrain_servers.py` | `gpu_instances = {0: [8040]}`. Port 8040 sits next to pyannote 8050, wtpsplit 8060 and whisper 8070-8075, and avoids datasette's 8001. |

### API

```
GET  /health -> {"status": "ok", "model": "speechbrain/spkrec-ecapa-voxceleb", "dimension": 192}

POST /embed   multipart/form-data
  file   = 16 kHz mono PCM wav (the content's 16k_mono_wav)
  params = {"segments": [[12.31, 15.9], [16.2, 16.5], ...],  # required, seconds, 0 <= start < end
            "min_duration": 1.0,                            # optional, float >= 0.1
            "max_duration": 30.0,                           # optional, float >= min_duration
            "batch_size": 16}                               # optional, int >= 1
200 -> {"model": "...", "dimension": 192,
        "embeddings": [[0.0123, ... 192 floats], null, ...]}  # request order; null = too short / past end of file
400 -> {"error": "..."}   bad params, bad segment, not 16 kHz mono, undecodable audio
500 -> {"error": "GPU out of memory: lower 'batch_size' or 'max_duration'."}
```

### Design decisions

| Topic | Decision | Why |
|---|---|---|
| Model | `speechbrain/spkrec-ecapa-voxceleb` (ECAPA-TDNN, 192-d, 16 kHz, VoxCeleb1+2) | SpeechBrain's reference speaker model. Public and not gated, so **no HF token needed**. The server reads the dimension at startup, so swapping models needs no code change. |
| SpeechBrain version | Pinned to **1.1.2** (`speechbrain_version`) | 1.0.x is reported to break with torchaudio ≥ 2.9 (`list_audio_backends` removed, `load` moved to torchcodec). 1.1 renamed `use_auth_token` → `token`. 1.1.2 imports fine with torchaudio 2.11. |
| Base image | The same pinned `rocm/pytorch@sha256:…` as pyannote (Phase 0.3) | Shared layers and one torch version. `rocm_pytorch_image` defaults to `rocm/pytorch:latest` like the other servers until it's pinned. |
| Protect ROCm torch | `pip list --format=freeze \| grep -iE '^(torch\|torchaudio)==' > /tmp/rocm_constraints.txt && pip install -c /tmp/rocm_constraints.txt …` | Without it, `pip install speechbrain` can replace the image's ROCm torch with a CUDA wheel from PyPI. With it, the build fails loudly instead. `pip list --format=freeze` also handles torch installed from a local wheel, which `pip freeze` would print as `torch @ file://…`. |
| Audio decoding | `soundfile` on the uploaded stream, `seek()` + `read()` per range. Require 16 kHz mono (reject anything else with 400). | **No torchcodec** (pyannote had to compile it from source). Memory stays bounded on a 6 h file, and nothing is kept on disk. |
| Short turns | Shorter than `min_duration` (default 1.0 s, after clipping to the file length): return `null` | ECAPA's error rate rises sharply under ~3 s, so recognition should only *use* turns ≥ 3 s. A single batch of chunks shorter than ~40 ms crashes the conv padding, hence the `min_duration >= 0.1` validation. |
| Long turns | Split into equal chunks ≤ `max_duration`; the turn embedding is the re-normalised mean of the L2-normalised chunk embeddings | Bounds GPU memory and padding. |
| Batching | Sort chunks longest first, pad each chunk to the batch maximum **with its own audio** (`np.pad(..., mode="wrap")`), `wav_lens = len / max_len` | `wav_lens` only masks the statistics (normalisation, pooling); the convolutions still see the padding. With zero padding, a 1 s turn's vector depended on the other turns in its batch (cosine 0.977 alone vs. padded to 30 s, real ECAPA architecture). With wrap padding it's 0.9998. Sorting keeps padding small, and an OOM shows up on the first batch. |
| Output | L2-normalised float32 vectors | Cosine similarity becomes a dot product, and `vec_distance_cosine` works directly in SQL. |
| Limits | `app.config["MAX_FORM_MEMORY_SIZE"] = 64 MiB` | Werkzeug ≥ 3.1 caps non-file form fields at 500 kB, so a VOD with thousands of turns would get HTTP 413. |
| Runtime | gunicorn `-w 1 --threads 1 --timeout 0`; `ENV HF_HUB_OFFLINE=1` after the build-time model download; `torch.cuda.empty_cache()` after each request | One request at a time on the GPU, no network at startup (reproducible), and peak memory is released for co-located Whisper and pyannote containers. |
| Server code | A **plain raw string** (`SERVER_SCRIPT = r'''…'''`) configured by `ENV` in the Dockerfile, still base64-injected as today | No doubled braces. A unit test can `compile()` it before a 20-minute build (see 8.3). |

### Implementation (done)

The server, its Dockerfile and its client are in `helpers/speechbrain_amd_server.py`, `helpers/speechbrain_client.py` and `run_speechbrain_servers.py`. On CPU, it was tested with the real ECAPA architecture (random weights, since the Hub isn't reachable from the test sandbox):
- run through Flask's test client and through gunicorn with the exact ENTRYPOINT arguments;
- the main case, clipping, short and long turns, padding consistency, all 400 cases (including NaN/Infinity), 20 000 segments, and a 25-minute request that isn't killed with `--timeout 0`.

**Not tested:** the image build on `rocm/pytorch` and the GPU runtime. Use the manual test below.

The build-time `from_hparams` runs on CPU. It downloads the model and also proves that `import speechbrain` works with the image's torchaudio, so an incompatibility fails at build time (once 1.4 is fixed). Don't mount a shared HF cache volume into this container: SpeechBrain's `savedir` entries are symlinks into `~/.cache/huggingface`.

### Pitfalls the server alone does not solve

- **Send exclusive turns.** `diarization` contains overlapping turns, so a range can contain two voices. Send the `exclusive_diarization` that the pyannote server now also returns (same `{speaker: [[start, end], …]}` shape) instead. Even exclusive turns give overlapped frames to the dominant speaker, so store each turn's overlap and ignore heavily overlapped turns for recognition.
- **Long VODs.** A 6 h VOD has thousands of turns and a ~700 MB wav. One request per content (the wav is uploaded once) works with `--timeout 0`. The response is about 4 KB per turn; rounding to 6 decimals roughly halves that.
- **pyannote already computes voiceprints.** `DiarizeOutput.speaker_embeddings` gives one centroid per label from pyannote's own embedding model (dimension unverified, likely 256). It's a free cross-check, but never mix those vectors with ECAPA ones.

### Manual test (on the GPU box)

1. `python run_speechbrain_servers.py`. The build log prints the two constraint lines, and the container log shows the model, dimension 192 and `cuda:0`.
2. `curl -s http://127.0.0.1:8040/health` should return `{"dimension": 192, "model": "...", "status": "ok"}`.
3. `curl -s -F file=@cache/16k_mono_wav/<X>.wav -F 'params={"segments": [[0.0, 5.0], [5.0, 5.3], [10.0, 70.0]]}' http://127.0.0.1:8040/embed`. Expect `[192-d, null, 192-d]`; the 60 s turn becomes 2 chunks, so 3 chunks in total.
4. Error cases, each should return 400: `[[5, 1]]`, an unknown key, `"min_duration": 1` (int, not float), no `file`, a text file as `file`, a 44.1 kHz file.
5. Sanity check on real speech: two long turns of the same speaker should have a clearly higher dot product than turns of different speakers (roughly 0.6–0.8 vs < 0.3, uncalibrated).
6. Load test: one full multi-hour VOD. Watch `rocm-smi` and the request time, and lower `batch_size` on OOM.

---

## Phase 3 — Database foundation (M)

**Implementation notes (done: migrations v1–v3).**
- `helpers/database.py` holds `MIGRATIONS`, applied by `connect()` in one transaction, after a `VACUUM INTO` backup in `cache/backups/`:
  - **v1** is the original schema.
  - **v2** rebuilds `contents` (only platform, external id and URL mandatory; `''`/`0` placeholders become `NULL`; new `channel_id`, `series`, `added_at`) and `files` (no `CHECK`; `variant` and `created_at`).
  - **v3** adds `diarizations`, `turns` and `turn_embeddings` as in 3.4.
- **Guarantees, tested:**
  - concurrent migrators (threads and processes) migrate once, and a failure is not retried by the other threads of a process;
  - a failing migration rolls back entirely;
  - ids of `contents`, `diarizations` and `turns` are never reused;
  - `files` rows of contents deleted without foreign keys are dropped by v2, and such pre-existing orphans never block a later migration;
  - a damaged database, or columns, indexes or triggers added by hand to `contents`/`files`, stop v2 with an error naming them; views keep working;
  - backups are written as `.partial`, fsynced, then renamed.
- **Gap:** contents ingested before v2 have no `channel_id`/`series`. Backfill them from their `ytdlp_info` JSON before Phase 5 groups speakers per channel. The French TV and radio extractors often give no channel either; derive a key from the URL path for those.
- **Still to do:**
  - The `failures` table (3.2) and the speaker tables of 3.4 (`speakers`, `speaker_samples`, `speaker_labels`) are left to the migration of the step that uses them; adding a table needs no rebuild. The speaker tables will follow the revised design: per-content voiceprints, and identities grouped per (person, channel).
  - The 3.3 helpers (repo-root paths, `upsert_content`, `contents_needing`) are not done yet.

### 3.1 Migrations with `PRAGMA user_version`

Replace `create_tables()` with an append-only list applied inside `connect()`, so every script migrates on first use and `create_sqlite_db.py` becomes optional.

```python
MIGRATIONS = [  # append-only: never edit an entry that has already been applied
    [CREATE_CONTENTS_V1, CREATE_FILES_V1],  # v1 = today's CREATE TABLE IF NOT EXISTS, verbatim (no-op on existing DBs)
    [...],                                  # v2 = 3.2
]

def migrate(db_conn):
    if db_conn.execute("PRAGMA user_version").fetchone()[0] >= len(MIGRATIONS):
        return
    db_conn.commit()
    backup(db_conn)                                  # VACUUM INTO 'backups/sniff-<ts>.db'
    old_isolation, db_conn.isolation_level = db_conn.isolation_level, None
    db_conn.execute("PRAGMA foreign_keys = OFF")     # ignored inside a transaction; with ON, DROP TABLE contents cascades into files
    try:
        db_conn.execute("BEGIN IMMEDIATE")           # a second script waits here instead of migrating twice
        try:
            version = db_conn.execute("PRAGMA user_version").fetchone()[0]  # re-read under the lock
            for v, statements in enumerate(MIGRATIONS[version:], start=version + 1):
                for statement in statements:
                    db_conn.execute(statement)
                db_conn.execute(f"PRAGMA user_version = {v}")
            if problems := db_conn.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError(f"Foreign key violations after migration: {problems[:5]}")
            db_conn.execute("COMMIT")
        except BaseException:
            db_conn.execute("ROLLBACK")              # user_version is transactional: all or nothing
            raise
    finally:
        db_conn.execute("PRAGMA foreign_keys = ON")
        db_conn.isolation_level = old_isolation
```

Rules:
- Each statement runs with `execute`, not `executescript`, which COMMITs implicitly and discards the `foreign_key_check` result.
- A migration that rebuilds a table must first drop, then recreate, any view or trigger that references it.
- Add `assert sqlite3.sqlite_version_info >= (3, 35)` (needed for `RETURNING` and `ON CONFLICT … DO UPDATE`).

### 3.2 Migration v2 — relax `files` and `contents`, record failures

```sql
-- files: no CHECK (a new feature = a new string; keep a FILE_TYPES tuple in Python to catch typos)
CREATE TABLE files_new (
    path TEXT PRIMARY KEY,                       -- relative to the repo root
    content_id INTEGER NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
    file_type TEXT NOT NULL,
    variant TEXT NOT NULL DEFAULT '',            -- e.g. subtitle language 'fr'
    created_at TEXT,
    UNIQUE (content_id, file_type, variant)
);
INSERT INTO files_new (path, content_id, file_type, variant)
    SELECT path, content_id, file_type,
           CASE WHEN file_type = 'ytdlp_subtitles' AND path LIKE '%.fr.srt' THEN 'fr' ELSE '' END
    FROM files;
DROP TABLE files;
ALTER TABLE files_new RENAME TO files;

-- contents: only identity is mandatory; extractors leave the rest out
CREATE TABLE contents_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,                      -- yt-dlp extractor_key
    external_id TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,                    -- webpage_url
    title TEXT,
    duration_s REAL,
    uploader TEXT,
    channel_id TEXT,
    series TEXT,                                 -- programme name: TF1 and LCI both come out as platform 'Wat'
    upload_date TEXT,                            -- ISO-8601 UTC; prefer release_timestamp for broadcasts
    language TEXT,
    added_at TEXT,
    UNIQUE (platform, external_id)
);
INSERT INTO contents_new (id, platform, external_id, url, title, duration_s, uploader, upload_date)
    SELECT id, platform, external_id, url, title, duration_s, uploader, upload_date FROM contents;
DROP TABLE contents;
ALTER TABLE contents_new RENAME TO contents;

-- failures: visible in datasette, not retried forever (DRM, geo-blocked, live, broken URL)
CREATE TABLE failures (
    id INTEGER PRIMARY KEY,
    step TEXT NOT NULL,                          -- 'download', 'transcribe', 'diarize', 'embed', ...
    content_id INTEGER REFERENCES contents(id) ON DELETE CASCADE,
    url TEXT,
    error TEXT NOT NULL,
    at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

Optional: `contents.info_json TEXT` holding `ydl.sanitize_info(info)` without the heavy keys (`formats`, `thumbnails`, `subtitles`, `automatic_captions`, `heatmap`, …). Any yt-dlp field is then a `json_extract` query, and a field you need to index can be added later as a generated column without another rebuild.

### 3.3 Small helpers in `helpers/database.py`

- `connect()` as a `contextlib.contextmanager` that commits and **closes**. Today `with sqlite3.connect() as c:` only commits; it never closes. Use `timeout=30` and run `migrate()` there.
- `ROOT = Path(__file__).resolve().parents[1]`. Store paths relative to `ROOT` and resolve them against it, so the scripts work from any working directory.
- `upsert_content(...)` (`INSERT … ON CONFLICT(platform, external_id) DO UPDATE … RETURNING`), `get_file(conn, content_id, file_type, variant="")` (returns `None` if the row is missing **or** the file is gone), `register_file(...)` (upsert), `file_path(content, file_type, ext)` (the one path convention, replacing five hand-built f-strings).
- `write_atomic(path, text)`: write to `path.part`, then `os.replace`, so a crash never leaves a half-written file that looks valid.
- `contents_needing(conn, have, missing)`: the to-do query for convention 1. **List the columns explicitly**: `SELECT c.*` into `Content(*row)` breaks silently when a migration adds a column.
- Register a `regexp(pattern, text)` SQL function, so word counts become SQL.

### 3.4 Migration v3 — diarization, voice embeddings, speakers

```sql
CREATE TABLE diarizations (                        -- one row per run: provenance + "done" marker (even with 0 turns)
    id INTEGER PRIMARY KEY,
    content_id INTEGER NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
    model TEXT NOT NULL,                           -- 'pyannote/speaker-diarization-community-1@<revision>'
    params TEXT NOT NULL DEFAULT '{}',             -- json.dumps(params, sort_keys=True)
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (content_id, model, params)
);
CREATE TABLE turns (
    id INTEGER PRIMARY KEY,
    diarization_id INTEGER NOT NULL REFERENCES diarizations(id) ON DELETE CASCADE,
    speaker TEXT NOT NULL,                         -- 'SPEAKER_00': only meaningful inside one diarization
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL CHECK (end_ms > start_ms),
    exclusive INTEGER NOT NULL CHECK (exclusive IN (0, 1)),  -- 1 = exclusive_speaker_diarization
    overlap_ms INTEGER NOT NULL DEFAULT 0          -- overlapped speech inside this turn
);
CREATE INDEX turns_by_diarization ON turns(diarization_id, exclusive, start_ms);
CREATE TABLE turn_embeddings (
    turn_id INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    model TEXT NOT NULL,                           -- 'speechbrain/spkrec-ecapa-voxceleb'
    embedding BLOB,                                -- L2-normalised float32 (sqlite_vec.serialize_float32); NULL = too short
    PRIMARY KEY (turn_id, model)
);
CREATE TABLE speakers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    notes TEXT
);
CREATE TABLE speaker_samples (                     -- enrollment = reference ranges: survives re-diarization and model changes
    id INTEGER PRIMARY KEY,
    speaker_id INTEGER NOT NULL REFERENCES speakers(id) ON DELETE CASCADE,
    content_id INTEGER NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    model TEXT,
    embedding BLOB                                 -- recomputable from the range
);
CREATE TABLE speaker_labels (                      -- who each diarization label is
    diarization_id INTEGER NOT NULL REFERENCES diarizations(id) ON DELETE CASCADE,
    speaker TEXT NOT NULL,
    speaker_id INTEGER REFERENCES speakers(id) ON DELETE CASCADE,  -- NULL = compared, unknown
    source TEXT NOT NULL CHECK (source IN ('human', 'auto')),      -- 'auto' rows never overwrite 'human' ones
    similarity REAL,
    PRIMARY KEY (diarization_id, speaker)
);
```

**Why plain BLOB tables rather than sqlite-vec `vec0` as the primary store.**
- Foreign-key cascades work, so deleting a content or a diarization removes its embeddings.
- Tools without the extension (datasette, the `sqlite3` CLI) can still open and modify the DB. With vec0 tables plus cleanup triggers, `DELETE FROM contents` fails with `no such module: vec0`.
- The model name sits on every row.
- Brute-force `vec_distance_cosine(e.embedding, :v)` is fine at this scale, and identification compares a handful of centroids anyway.

If corpus-wide "find this voice" queries become slow, add a **rebuildable** `vec0` index later, with `content_id` as a metadata column. KNN's `k` is applied *before* joined `WHERE` filters, so `… AND k = 10` joined to `content_id != ?` silently returns nothing unless the filter is a vec0 metadata column.

---

## Phase 4 — Diarization into the DB (README TODO 2, M)

| # | Change |
|---|---|
| 4.1 | **Server** (`helpers/pyannote_amd_server.py`). It already returns `exclusive_diarization` next to `diarization` (Phase 1, grouped by speaker). For the DB, consider `output.serialize()`, which in pyannote.audio 4.x gives both as chronological lists. Also return `speaker_embeddings` (skip all-zero rows, read the dimension instead of assuming it). Keep `--timeout 0` (1.5). (Done: the server passes pyannote an in-memory waveform read with `soundfile`, so the torchcodec source build is gone.) |
| 4.2 | **Client.** `wav_to_pyannote_result(address, wav_path, params=None) -> dict`, with no forced speaker count. |
| 4.3 | **`diarize_contents.py`** (new). Select contents with a `16k_mono_wav` and no `diarizations` row for (model, params). POST to the server, `write_atomic` the raw JSON as `pyannote_result`, then insert the `diarizations` row and all turns (regular + exclusive, with `overlap_ms`) **in one transaction**. Use the dispatch-to-many-servers loop from 1.9 if you run several pyannote servers. |
| 4.4 | **Long VODs.** VBx clustering builds a condensed distance matrix over ~1 embedding per second per local speaker. Peak RAM is roughly `n² × 8` bytes: ~3 GB at n = 20 k (≈ 6 h), ~13 GB at n = 40 k. Measure with `docker stats` on your longest VOD. If needed, diarize ~2 h windows and relabel across windows using the per-window `speaker_embeddings`. |
| 4.5 | **Re-diarizing** a content is `DELETE FROM diarizations WHERE …`; turns, embeddings and labels cascade. Refuse to do it when `speaker_labels.source = 'human'` rows exist, unless `--force` is passed. |

## Phase 5 — Embeddings and speaker identity (README TODO 4, M–L)

| # | Script | What it does |
|---|---|---|
| 5.1 | `embed_speaker_turns.py` | Selects diarizations whose exclusive turns have no `turn_embeddings` row for the model. Sends one request per content (wav uploaded once) and inserts all vectors, including `NULL`s for too-short turns so they aren't retried, in one transaction. |
| 5.2 | `listen_speaker.py CONTENT_ID LABEL` (~40 lines) | `ffmpeg -ss/-t` cuts the 3 longest exclusive turns of a label into one clip and prints their transcript. **Without this, nobody can tell who `SPEAKER_01` is.** |
| 5.3 | `enroll_speaker.py NAME CONTENT_ID START END [...]` (or `--label SPEAKER_01`) | Creates or gets the `speakers` row and stores `speaker_samples` (ranges + their embeddings). |
| 5.4 | `identify_speakers.py` | For each (diarization, label): a duration-weighted mean of its turn embeddings, using turns ≥ 3 s with little overlap. Compares it with each speaker's centroid (mean of their samples) and writes `speaker_labels` with `source='auto'` and the similarity, or `speaker_id = NULL` below the threshold. It **never overwrites `'human'` rows.** |
| 5.5 | `evaluate_speakers.py` + a small ground-truth set | Hand-label 3–5 contents (a TV news broadcast, a radio show, a Twitch VOD). Print the speaker count error and the identification precision/recall at a few thresholds. SpeechBrain's `verify_batch` uses cosine > 0.25 for single VoxCeleb utterance pairs, but centroids of TV/radio/Twitch audio behave differently, so **calibrate before trusting automatic labels**. Re-run after every image rebuild. |

Later: link unknown voices across a channel's contents ("unk-12"), with a one-UPDATE promotion to a named speaker.

**Privacy/legal scope.** Decide this before building enrollment. Voiceprints used to identify named people are biometric data under GDPR Art. 9. A private research setup is a different situation from publishing speaker-attributed charts. Reasonable defaults: enroll public figures only in their public role, never name unknown clusters, keep the DB and audio private, have a human check before publishing, and make deleting a speaker remove everything derived from them (the cascades above do that). This is not legal advice.

## Phase 6 — Transcripts in the DB, "who said what", sentences (README TODO 3, M)

- **`words` table** (`content_id`, `idx`, `text`, `norm`, `start_ms`, `end_ms`, `prob`, `turn_id`), filled from the whisper `verbose_json`. With `max_len=1` + `split_on_word`, one segment is one word. Move the DTW + VAD-offset logic of `whisper_result_to_srt` (`helpers/whisper_client.py:121-132`) into one `whisper_result_to_words()`, and make the SRT export use it, so the DB and the SRT always agree.
- **Pin the whisper.cpp commit** (`helpers/whisper_amd_server.py:13` clones master). Which timestamps are VAD-mapped (segment yes, token no, today) is an implementation detail that is changing upstream. A silent change would shift every word time.
- **Alignment:** assign each word to the exclusive turn containing its midpoint (one SQL `UPDATE` or a two-pointer loop).
- **wtpsplit:** accept a list of texts (`helpers/wtpsplit_amd_server.py:16` only accepts `str`; `SaT.split` on a list returns a generator, so wrap it in `list()`). Split per-speaker runs of words, so sentences never straddle speakers. Map sentences back to words by character offsets (`strip_whitespace=False` keeps `"".join(sentences) == text`). Store them in a `sentences` table with an FTS5 index (`tokenize='unicode61 remove_diacritics 2'`; use prefix queries like `pete*` to match both *péter* and *pété*). First compare against a trivial "split on `.?!…`" baseline.
- **`plot_word_counts.py`**: the same matplotlib code, but the data comes from one SQL query (it currently reads the pre-DB `old_cache/` layout). Filter by speaker once Phase 5 exists, which keeps TTS donations and game dialogue out of the counts.

## Phase 7 — Sources and scheduled runs (README TODO 1, M)

1. **Split downloading from GPU work.** Move `sniff_and_transcript_channel_audios.py:70-88` into `sniff_channel_audios.py`, which imports no client and needs no GPU server. Add `transcribe_contents.py`, driven by the DB. Sources expire (Twitch VOD retention, TV/radio replays), so downloads must not wait for the GPU box. For a first backfill, process the oldest first.
2. **`sources` table + `sniff_sources.py`** (name, url, enabled, language, min/max speakers, last_listed_at). `ls_channel_videos` should return the flat entries (title, timestamp, duration), not bare URLs, and accept `playlist_items` for incremental runs. For Twitch, use `/videos?filter=archives`; the default includes highlights that duplicate archive audio.
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
6. **Scheduling:** a `run_pipeline.sh` that runs each step in turn, wrapped in `flock -n` so cron runs never overlap. Add an optional `--detach` to `run_*_servers.py` with `restart_policy={"Name": "unless-stopped"}`.
7. **Disk:** today the mp3 (192 kbps, ~86 MB/h) and the wav (~115 MB/h) are both kept forever. Options:
   - download `bestaudio` with no mp3 re-encode, and keep a single 16 kHz mono **FLAC** (estimated ~half the wav) as the canonical file, after checking that every server reads it;
   - add a `cleanup_cache.py` that evicts intermediate files once all steps are done. Keep the canonical audio for sources that expire.
8. **Language per source** instead of the hard-coded `"fr"` (`sniff_and_transcript_channel_audios.py:53`). RFI and TV5MONDE are multilingual: use `language=auto` there and store the detected language.

## Phase 8 — Hygiene (interleave with the above)

| # | Item |
|---|---|
| 8.1 | `requirements.txt` for the host: `docker requests sqlite-vec yt-dlp yt-dlp-ejs matplotlib`; `datasette` and `pytest`/`ruff` as dev extras. The README's pip line is missing matplotlib. |
| 8.2 | **README fixes.** On Linux, use **Docker Engine**, not Docker Desktop: Desktop runs containers in a VM and cannot pass `/dev/kfd` to them. Whisper exposes `/health`, not `/ready`. If vec0 tables are ever added, datasette needs `--load-extension "$(python3 -c 'import sqlite_vec; print(sqlite_vec.loadable_path())')"`. |
| 8.3 | **Tests** (pytest, no GPU/network/Docker): `subtitles.py`, `dtw_to_srt_timestamp`, `whisper_result_to_srt`; migrations on `:memory:` with sqlite-vec loaded; the DB helpers; `compile(SERVER_SCRIPT, …)` for every server, which catches syntax errors before a long image build. Run `ruff` for lint and format. |
| 8.4 | **Reproducible, secret-free images.** Pin the base image by digest (0.3), pip versions via constraints, the whisper.cpp commit and HF model `revision=`. Add `ENV HF_HUB_OFFLINE=1` after build-time downloads: today `from_pretrained` checks the Hub at every container start, so results can change without any image change. Today the HF token is baked into an image layer and into `/app/pyannote_server.py`. Pass it at runtime (`environment={"HF_TOKEN": …}` + an HF cache volume) or download on the host. |
| 8.5 | **Readable server modules:** `SERVER_SCRIPT = r'''…'''` with configuration through `ENV`, instead of f-strings with doubled braces. Keep one file per server and the base64 injection. |
| 8.6 | Bind published ports to `127.0.0.1` unless remote GPU boxes are intended: whisper.cpp's `/load` lets anyone on the LAN swap the model. Label containers, so `stop_*` still finds them after an image rebuild. |
| 8.7 | Explicit imports instead of `from helpers.x import *` (`plot_pipi_caca_panard.py` currently gets `json` through `helpers.yt_dlp`). Move `test.py` to a git-ignored `scratch/`. Use a global `__pycache__/` in `.gitignore`. |
| 8.8 | Optional: a named volume for the MIOpen kernel cache (`~/.cache/miopen`), so the first requests after each container start aren't slow. |

---

## Quick wins (each ≤ 30 min)

1. 1.1 + 1.2 + 1.3: the transcription pipeline runs again.
2. 1.4: build failures become visible.
3. 1.5 + 1.6: diarization works on long VODs with a real speaker count.
4. 0.1–0.3: DB backed up and working images pinned before anything else changes.
5. 1.13: the sparsify newline bug (two lines).

## How this review was done / what is unverified

Six independent reviews (correctness, model servers, data model, structure, SpeechBrain design, roadmap research) were each challenged by an adversarial verifier, then a completeness pass checked the result against the objectives. The DB designs and the SpeechBrain server logic were exercised with SQLite 3.45 / sqlite-vec 0.1.9 and stubbed models. **Not verified:** anything on a ROCm GPU (SpeechBrain on ROCm, memory use, speed), the exact current `rocm/pytorch:latest` contents, and the SpeechBrain/pyannote version numbers, which move quickly. Check those at build time; the pins in 0.3 and 8.4 make that a one-time job.
