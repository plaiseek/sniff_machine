from helpers.pipeline import run_steps, transcription_step

import helpers.database as db
import servers
import sys

# Contents with no audio file (e.g. subtitles only) are left out: their audio is never downloaded
with db.connect() as db_conn:
    contents = db.get_contents_without_file(
        db_conn, "whisper_result", ["16k_mono_wav", "ytdlp_audio"]
    )
if len(contents) == 0:
    print("Nothing to transcribe.")
    sys.exit(0)
print(f"{len(contents)} audios to transcribe.")

# A registered wav or mp3 deleted from disk is rebuilt, re-downloading the mp3 if needed.
# The Whisper servers are listed in servers.py.
report = run_steps(contents, [transcription_step(servers.WHISPER_SERVERS, "fr")])
sys.exit(report.exit_code())
