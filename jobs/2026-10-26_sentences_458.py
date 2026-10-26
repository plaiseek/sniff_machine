"""Export: the sentences of content 458 as CSV, each with its speaker label, who that label is when
known, its start time (H:MM:SS) and a link to that moment ("Exporte en CSV les phrases de la vidéo
458 avec le locuteur (et son nom s'il est connu), l'heure de début et un lien vers ce moment.").

Only reads the database. Run it from the repository root with
`python3.11 jobs/2026-10-26_sentences_458.py`; re-running it writes the CSV again.
"""

import csv, io, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT); sys.path.insert(0, str(ROOT))
import helpers.database as db
from helpers.speakers import describe_label
from helpers.transcripts import content_link, hms

CONTENT_ID = 458
OUTPUT = Path("exports") / Path(__file__).stem / "sentences_458.csv"  # exports/2026-10-26_sentences_458/

with db.connect() as db_conn:
    contents = db.find_contents(db_conn, ids=[CONTENT_ID])
    if not contents:
        sys.exit(f"No content {CONTENT_ID} in the database")
    content = contents[0]
    sentences = db.get_sentences(db_conn, content.id)
    if not sentences:
        sys.exit(f"Content {content.id} has no stored sentences: split them first (pipeline/split_sentences.py)")
    # The speaker labels of the sentences are those of the diarization the stored words are aligned to
    diarization_id = db.get_transcription_diarization(db_conn, content.id)
    labels = db.get_speaker_labels(db_conn, diarization_id) if diarization_id is not None else {}

rows = io.StringIO()
writer = csv.writer(rows)
writer.writerow(["debut", "locuteur", "nom", "phrase", "lien"])
for first_idx, last_idx, start_ms, end_ms, speaker, text in sentences:
    # describe_label: "" when the label was never identified; an automatic name is a guess with its similarity
    who = describe_label(labels, speaker) if speaker is not None else ""
    writer.writerow([hms(start_ms), speaker or "", who, text, content_link(content, start_ms)])
db.write_atomic(OUTPUT, rows.getvalue())
named = sum(1 for _, _, _, _, speaker, _ in sentences if speaker is not None and describe_label(labels, speaker))
print(f"Wrote {OUTPUT} ({len(sentences)} sentences of content {content.id}, {named} with a known speaker: {content.title})")
