"""Export: the sentences of content 418 as CSV, each with its speaker, its start time (H:MM:SS) and a
link to that moment ("Exporte en CSV les phrases de la vidéo 418 avec, pour chacune, le locuteur,
l'heure de début (H:MM:SS) et un lien YouTube vers ce moment.").

Only reads the database. Run it from anywhere with `python3.11 jobs/2026-10-26_sentences_418.py`;
re-running it writes the CSV again.
"""

import csv, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT); sys.path.insert(0, str(ROOT))
import helpers.database as db
from helpers.transcripts import content_link, hms

CONTENT_ID = 418
OUTPUT = "exports/2026-10-26_sentences_418/2026-10-26_sentences_418.csv"

with db.connect() as db_conn:
    contents = db.find_contents(db_conn, ids=[CONTENT_ID])
    if not contents:
        sys.exit(f"No content {CONTENT_ID}")
    content = contents[0]
    sentences = db.get_sentences(db_conn, content.id)
    if not sentences:
        sys.exit(f"Content {content.id} has no stored sentences (split them with pipeline/split_sentences.py)")
    row = db_conn.execute("SELECT MAX(id) FROM diarizations WHERE content_id = ?", (content.id,)).fetchone()
    # Who the labels are, only when a person or speakers/identify_speakers.py said so (auto: a guess)
    labels = db.get_speaker_labels(db_conn, row[0]) if row[0] is not None else {}

with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["debut", "locuteur", "identite", "phrase", "lien"])
    for first_idx, last_idx, start_ms, end_ms, speaker, text in sentences:
        identity = ""
        if speaker in labels:
            _, name, source, similarity = labels[speaker]
            identity = f"{name or 'inconnu'} ({source}" + (f", {similarity:.2f})" if similarity is not None else ")")
        writer.writerow([hms(start_ms), speaker or "", identity, text, content_link(content, start_ms)])
print(f"Wrote {OUTPUT} ({len(sentences)} sentences of content {content.id}: {content.title})")
