# Sniff Machine

This project provides a pipeline for automatically downloading and transcribing videos from YouTube, Twitch (and, soon, many other plateforms) using OpenAI's Whisper model deployed via Docker containers (specifically optimized for AMD GPUs).

## Installation

### Prerequisites

1. **Docker Desktop**: [https://docs.docker.com/desktop/](https://docs.docker.com/desktop/)

2. **Python Dependencies**:
   ```bash
   pip install yt-dlp whisper docker requests
   ```

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

## TODO

1. Refactor the usage of YT_DLP to follow `https://github.com/yt-dlp/yt-dlp#embedding-examples`
2. Find a way to retrieve (or scrap) Videos list of france.tv etc...
3. Try pyannote for Diarization
4. Find a way to identify speakers (voice recognition for famous people, LLM analysis for others when presented in text)