import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)  # the database and cache/ paths are relative to the repository root
sys.path.insert(0, str(ROOT))

from helpers.pipeline import diarization_step, run_steps
from helpers.transcripts import update_transcripts
import helpers.database as db
import servers

# Passed to pyannote: {} lets it find the number of speakers, or e.g. {"num_speakers": 2},
# {"min_speakers": 2, "max_speakers": 6}. A content is diarized once per (model, params).
params = {}

# Contents with an audio file and no diarization yet; contents without audio (e.g. subtitles only)
# are left out. The Pyannote servers are listed in servers.py.
step = diarization_step(servers.PYANNOTE_SERVERS, params)
with db.connect() as db_conn:
    contents = [
        content
        for content in db.get_contents_with_file(db_conn, ["16k_mono_wav", "ytdlp_audio"])
        if not step.is_done(db_conn, content)
    ]
if len(contents) == 0:
    print("Nothing to diarize.")
    sys.exit(0)
print(f"{len(contents)} audios to diarize.")

# A registered wav or mp3 deleted from disk is rebuilt, re-downloading the mp3 if needed
report = run_steps(contents, [step])
# The words of the transcriptions (if any) follow the new diarizations
with db.connect() as db_conn:
    failures = update_transcripts(db_conn, [content.id for content in contents])
sys.exit(report.exit_code() or (1 if failures else 0))
