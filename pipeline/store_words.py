"""Store the words of the transcriptions in the database, with the speaker turn each falls in.

    python3.11 pipeline/store_words.py              # every transcribed content
    python3.11 pipeline/store_words.py 7 243        # only these contents

Stores the words of each transcription result (cache/whisper_result/) not stored yet, then gives
the words of each content diarized since the turn of its latest diarization that their middle
falls in. Jobs, pipeline/transcript_pending_audios.py and pipeline/diarize_contents.py do it too: run it after
transcribing or diarizing some other way. Running it again only does what is new. No server needed.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)  # the database and cache/ paths are relative to the repository root
sys.path.insert(0, str(ROOT))

import helpers.database as db
from helpers.transcripts import update_transcripts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("content_ids", type=int, nargs="*", help="contents to update (default: all)")
    args = parser.parse_args()
    with db.connect() as db_conn:
        failures = update_transcripts(db_conn, args.content_ids or None)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
