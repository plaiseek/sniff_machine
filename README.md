# Sniff Machine

A pipeline for automatically downloading and transcribing videos from YouTube, Twitch (and, soon, many other platforms). Everything is tracked in a SQLite database, and the heavy lifting (transcription, diarization, sentence segmentation) is done by GPU-accelerated model servers deployed as Docker containers (specifically optimized for AMD GPUs / ROCm).

## Architecture

### Database

A SQLite database (`cache/test.db`, see [helpers/database.py](helpers/database.py)) keeps track of:

- **`contents`**: every video/audio content sniffed (platform, external id, URL, title, duration, uploader, upload date).
- **`files`**: files produced for each content (`ytdlp_info`, `ytdlp_audio`, `ytdlp_subtitles`, `16k_mono_wav`, `whisper_result`, `pyannote_result`), stored under `cache/`.

All pipeline steps are idempotent: if a file is already registered in the database, it is not re-downloaded / re-computed.

### Model servers (Docker, AMD/ROCm)

Each model runs as an HTTP server inside a Docker container built and managed by the `run_*_servers.py` scripts (images are built on first run):

| Server | Script | Model | Default port(s) |
|--------|--------|-------|-----------------|
| Whisper | [run_whisper_servers.py](run_whisper_servers.py) | [whisper.cpp](https://github.com/ggml-org/whisper.cpp) `large-v3` + Silero VAD, HIP build | 8070, 8071 |
| Pyannote | [run_pyannote_servers.py](run_pyannote_servers.py) | `pyannote/speaker-diarization-community-1` | 8050 |
| wtpsplit | [run_wtpsplit_servers.py](run_wtpsplit_servers.py) | SaT `sat-12l-sm` (sentence segmentation) | 8060 |

Python clients for these servers live in [helpers/](helpers/) (`whisper_client.py`, `pyannote_client.py`, `wtpsplit_client.py`). All servers expose a `/ready` endpoint; inference endpoints are `/inference` (Whisper), `/diarize` (Pyannote) and `/split` (wtpsplit).

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

5. **Hugging Face token** (for Pyannote only): accept the conditions of [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1), then put your token in a `hf.token` file at the root of the project.

## Usage

1. **Create the database** (first time only):
   ```bash
   python create_sqlite_db.py
   ```

2. **Start the model servers** you need. In each `run_*_servers.py`, adjust `gpu_instances` (a `{device_id: [ports]}` mapping) and the build parameters (`gfx_version` for Whisper, `sat_model` for wtpsplit) to your hardware, then:
   ```bash
   python run_whisper_servers.py    # transcription
   python run_pyannote_servers.py   # speaker diarization
   python run_wtpsplit_servers.py   # sentence segmentation
   ```
   Each script builds the Docker image if needed, starts the containers, and streams their logs until you hit Ctrl+C.

3. **Run a sniffing pipeline**:

   - **Transcribe a channel's audio with Whisper** — edit [sniff_and_transcript_channel_audios.py](sniff_and_transcript_channel_audios.py) to set the target channel URL and the addresses of your Whisper servers, then:
     ```bash
     python sniff_and_transcript_channel_audios.py
     ```
     For each video of the channel it downloads the audio (mp3), converts it to 16 kHz mono wav, and dispatches transcription jobs to all the Whisper servers in parallel. Results are saved as JSON in `cache/whisper_result/` and registered in the database.

   - **Download a channel's existing subtitles** — edit [sniff_channel_subtitles.py](sniff_channel_subtitles.py) to set the target channel URL(s), then:
     ```bash
     python sniff_channel_subtitles.py
     ```

4. **Explore the database**:
   ```bash
   python3 -m datasette cache/test.db --port 8001
   ```

## TODO

1. Find a way to retrieve (or scrap) videos list of france.tv etc...
2. Integrate the Pyannote diarization server into the sniffing pipeline
3. Integrate the wtpsplit sentence segmentation into the sniffing pipeline
4. Implement a SpeechBrain pipeline for speaker recognition

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
