from helpers.pipeline import embedding_step, run_steps

import helpers.database as db
import servers
import sys

# Voice embeddings of the exclusive turns of every diarization, from the SpeechBrain servers
# listed in servers.py. Run it after diarize_contents.py (or a job that diarizes): only the turns
# without an embedding for the servers' model are sent.
step = embedding_step(servers.SPEECHBRAIN_SERVERS)
with db.connect() as db_conn:
    contents = [content for content in db.find_contents(db_conn) if not step.is_done(db_conn, content)]
if len(contents) == 0:
    print("Nothing to embed.")
    sys.exit(0)
print(f"{len(contents)} diarized audios with turns to embed.")

report = run_steps(contents, [step])
sys.exit(report.exit_code())
