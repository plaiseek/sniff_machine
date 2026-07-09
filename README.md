# Sniff Machine

This project provides a pipeline for automatically downloading and transcribing videos from YouTube, Twitch (and, soon, many other plateforms) using OpenAI's Whisper model deployed via Docker containers (specifically optimized for AMD GPUs).

## Installation

### Prerequisites

1. **Docker Desktop**: [https://docs.docker.com/desktop/](https://docs.docker.com/desktop/)

2. **Python Dependencies**:
   ```bash
   pip install sqlite-vec yt-dlp docker requests onnxruntime wtpsplit[onnx-cpu]
   ```

3. JS runtime for YT_DLP: 

   [https://docs.deno.com/runtime/getting_started/installation/](https://docs.deno.com/runtime/getting_started/installation/)
   [https://github.com/yt-dlp/yt-dlp/wiki/EJS#option-1-install-the-yt-dlp-ejs-python-package](https://github.com/yt-dlp/yt-dlp/wiki/EJS#option-1-install-the-yt-dlp-ejs-python-package)

## Usage

1. **Start the Whisper Servers**:
   Modify `run_whisper_servers.py` to include the `gfx_version`, the device Ids and ports for your Whispers servers, then :
   ```bash
   python run_whisper_servers.py
   ```

2. **Run Transcription**:
   Modify `transcript_twicth_vods.py` to include the URL of the target Twicth channel and the URLs of your Whisper servers to use, then : 
   ```bash 
   python transcript_twicth_vods.py
   ```

3. **Explore the database**
   ```bash
   python3 -m datasette cache/test.db --port 8001
   ```

## TODO

1. Find a way to retrieve (or scrap) Videos list of france.tv etc...
2. Implement a pyannote pipeline for diarization
3. Implement a SpeechBrain pipeline for speacker recognition

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
