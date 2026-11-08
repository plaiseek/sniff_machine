"""Job: <write the request here, e.g. "fetch, transcribe and diarize the videos of @name uploaded in March 2026">

Copied from jobs/job_template.py: only the settings below were edited. Run it from the repository
root with `python3.11 jobs/<this file>.py` (`--status` only says whether it is running). Re-running it
resumes: what is already in the database is skipped. Exit codes: 0 done, 1 some contents failed
(re-run to retry them), 2 invalid settings (or the video disk missing), 3 already running, 4 no
server of a step can be used, 5 a server is probably stuck (it needs a restart), 6 downloads
stopped: a platform refuses this machine for now (re-run later, not now).
"""

# ======================== Settings: edit only these lines ========================

# Channels, playlists or show pages to list (yt-dlp URLs), e.g.
#   "https://www.youtube.com/@name/videos"   (a YouTube channel's videos; "/streams" for its past lives)
#   "https://www.twitch.tv/name/videos?filter=archives"   (a Twitch channel's past broadcasts)
LISTING_URLS = []

# Single contents, e.g. ["https://www.youtube.com/watch?v=XXXXXXXXXXX"]
CONTENT_URLS = []

# Keep only the contents of LISTING_URLS uploaded from / to these dates, inclusive ("YYYY-MM-DD"), or None
UPLOADED_FROM = None
UPLOADED_TO = None

# What to do: "video" (the full video, at most 720p, to cache/ytdlp_video: a link to the video
# disk; the audio is then made from it, no mp3 is downloaded), "download" (audio), "transcribe"
# (whisper), "diarize" (pyannote: who speaks when), "embed" (speechbrain: voice embeddings of the
# diarized turns, run after "diarize", which speakers/identify_speakers.py needs), "split" (wtpsplit:
# the transcription in sentences, each of one speaker, run after "transcribe" and "diarize"; needs
# no audio). The words of the transcriptions are stored in the database by any job that
# transcribes, diarizes or splits. The audio is always downloaded when another step needs it.
# ["download"] alone also fetches again the audio deleted from cache/ of contents already
# transcribed or diarized. "video" downloads the video of each content before its audio: the
# servers wait for it.
STEPS = ["video", "transcribe", "diarize"]

# Transcription language: a 2-letter code ("fr", "en", ...) or "auto"
LANGUAGE = "fr"

# Diarization: {} lets pyannote find the number of speakers; or e.g. {"num_speakers": 2},
# {"min_speakers": 2, "max_speakers": 6}
DIARIZATION_PARAMS = {}

# True: only list the selected contents and what is already done for them (no download, no GPU)
DRY_RUN = True

# ================================ End of settings ================================

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)  # the database and cache/ paths are relative to the repository root
sys.path.insert(0, str(ROOT))

from helpers.jobs import run_job

sys.exit(
    run_job(
        __file__,
        LISTING_URLS,
        CONTENT_URLS,
        UPLOADED_FROM,
        UPLOADED_TO,
        STEPS,
        LANGUAGE,
        DIARIZATION_PARAMS,
        DRY_RUN,
    )
)
