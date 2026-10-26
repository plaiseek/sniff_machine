"""Find who said something: the sentences of the transcriptions that match a full-text query.

    python3.11 explore/search_transcripts.py "knafo mairie"              # sentences with both words
    python3.11 explore/search_transcripts.py '"mairie de paris"'         # this phrase
    python3.11 explore/search_transcripts.py "pete*"                     # péter, pété, pète... (case and accents ignored)
    python3.11 explore/search_transcripts.py "knafo" --uploader Psyhodelik --speaker "Jean Dupont" --limit 50

The query is in SQLite FTS5 syntax: words (all of them), "a phrase", prefix*, OR, NOT, NEAR(a b, 5).
Prints each sentence with its content, time, speaker (who the label is, when known) and a link to
that moment, oldest content first. Searches the sentences stored by pipeline/split_sentences.py.
"""

import argparse
import os
import sqlite3
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)  # the database and cache/ paths are relative to the repository root
sys.path.insert(0, str(ROOT))

import helpers.database as db
from helpers.speakers import describe_label
from helpers.transcripts import content_link, hms


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("query", help="FTS5 query, e.g. 'knafo', '\"mairie de paris\"', 'pete*'")
    parser.add_argument("--uploader", help="only the contents of this channel (part of its name)")
    parser.add_argument("--content", type=int, action="append", help="only this content (repeatable)")
    parser.add_argument("--speaker", help="only the sentences of the labels named so (by a person or automatically)")
    parser.add_argument("--limit", type=int, default=30, help="sentences shown (default 30)")
    args = parser.parse_args()

    where, params = ["sentences_fts MATCH ?"], [args.query]
    if args.uploader:
        where.append("instr(casefold(c.uploader), casefold(?)) > 0")
        params.append(args.uploader)
    if args.content:
        where.append(f"s.content_id IN ({', '.join('?' for _ in args.content)})")
        params += args.content
    if args.speaker:
        where.append("p.name = ?")
        params.append(args.speaker)
    query = f"""SELECT s.content_id, s.start_ms, s.diarization_id, s.speaker, s.text
FROM sentences_fts f JOIN sentences s ON s.id = f.rowid JOIN contents c ON c.id = s.content_id
LEFT JOIN speaker_labels l ON l.diarization_id = s.diarization_id AND l.speaker = s.speaker
LEFT JOIN speakers p ON p.id = l.speaker_id
WHERE {' AND '.join(where)}"""

    with db.connect() as db_conn:
        if not db_conn.execute("SELECT 1 FROM sentences LIMIT 1").fetchone():
            sys.exit("No sentences stored yet: run python3.11 pipeline/split_sentences.py")
        try:
            total = db_conn.execute(f"SELECT COUNT(*) FROM ({query})", params).fetchone()[0]
            rows = db_conn.execute(query + " ORDER BY c.upload_date, c.id, s.start_ms LIMIT ?", params + [args.limit]).fetchall()
        except sqlite3.OperationalError as e:
            sys.exit(f"Invalid query {args.query!r}: {e} (see https://www.sqlite.org/fts5.html#full_text_query_syntax)")
        contents, labels = {}, {}
        for content_id, _, diarization_id, _, _ in rows:
            contents.setdefault(content_id, db.get_content(db_conn, content_id))
            if diarization_id is not None and diarization_id not in labels:
                labels[diarization_id] = db.get_speaker_labels(db_conn, diarization_id)

    previous = None
    for content_id, start_ms, diarization_id, speaker, text in rows:
        content = contents[content_id]
        if content_id != previous:
            print(f"\nContent {content_id} ({(content.upload_date or '?')[:10]}): {content.title}")
            previous = content_id
        who = describe_label(labels.get(diarization_id, {}), speaker) if speaker else ""
        speaker_text = f"{speaker} = {who}" if who else speaker or "speaker unknown"
        print(f"  [{hms(start_ms)}] {speaker_text}  {content_link(content, start_ms)}")
        print(textwrap.fill(text, 100, initial_indent="    ", subsequent_indent="    "))
    print(f"\n{total} sentences match" + (f", the first {len(rows)} shown (--limit)." if total > len(rows) else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
