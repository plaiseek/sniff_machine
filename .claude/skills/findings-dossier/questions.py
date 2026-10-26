"""List the questions asked to an LLM (explore/ask_transcripts.py) and what they found, read-only.

    python3.11 .claude/skills/findings-dossier/questions.py         # every question
    python3.11 .claude/skills/findings-dossier/questions.py 3       # question 3: the arguments to show or export it

A question is found again (--show, --export) only with its exact text, criteria and speaker: this
gives them, with the criteria file of criteria/ whose content is the one stored, and --question N
(the same text may also have been asked with another model or settings).
"""

import pathlib
import shlex
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "cache" / "test.db"
CRITERIA = "\n\nCriteria:\n"  # helpers.questions.question_text() puts the criteria after this

if not DB_PATH.is_file():
    sys.exit(f"No database at {DB_PATH}")
db_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)


def criteria_file(criteria: str) -> str | None:
    """The file of criteria/ holding these criteria (read as explore/ask_transcripts.py reads it)."""
    for path in sorted((ROOT / "criteria").glob("*.txt")):
        try:
            if path.read_text(encoding="utf-8").strip() == criteria:
                return str(path.relative_to(ROOT))
        except (OSError, UnicodeDecodeError):
            continue  # not a criteria file explore/ask_transcripts.py could read
    return None


rows = db_conn.execute(
    """SELECT q.id, q.text, q.speaker, q.model, q.settings, q.created_at,
    (SELECT COUNT(*) FROM question_contents c WHERE c.question_id = q.id),
    (SELECT COUNT(*) FROM findings f WHERE f.question_id = q.id AND f.verdict = 'yes'),
    (SELECT COUNT(*) FROM findings f WHERE f.question_id = q.id AND f.verdict = 'unsure'),
    (SELECT COUNT(*) FROM findings f WHERE f.question_id = q.id AND f.verdict = 'no')
    FROM questions q ORDER BY q.id"""
).fetchall()
if len(sys.argv) > 2 or (len(sys.argv) == 2 and not sys.argv[1].isdigit()):
    sys.exit(__doc__.strip())
wanted = int(sys.argv[1]) if len(sys.argv) == 2 else None
if not rows:
    sys.exit("No question was asked yet (see the ask-transcripts skill).")
if wanted is not None and wanted not in [row[0] for row in rows]:
    sys.exit(f"No question {wanted}: run this script without a number to list them.")

for question_id, text, speaker, model, settings, created_at, asked, yes, unsure, no in rows:
    if wanted is not None and question_id != wanted:
        continue
    question, _, criteria = text.partition(CRITERIA)
    path = criteria_file(criteria) if criteria else None
    print(f"Question {question_id}: {question}")
    print(f"  criteria: {path or ('none' if not criteria else 'NOT FOUND in criteria/ (the file was edited or removed since)')}"
          f"; speaker: {speaker or 'anyone'}; {model}, {settings}; since {created_at}")
    print(f"  asked on {asked} contents: {yes} yes, {unsure} unsure, {no} no")
    if wanted is None:
        continue
    contents = db_conn.execute(
        """SELECT c.uploader, COUNT(*), MIN(substr(c.upload_date, 1, 10)), MAX(substr(c.upload_date, 1, 10))
        FROM question_contents q JOIN contents c ON c.id = q.content_id WHERE q.question_id = ? GROUP BY 1 ORDER BY 2 DESC""",
        (question_id,),
    ).fetchall()
    for uploader, count, first, last in contents:
        print(f"  {count} contents of {uploader!r}, uploaded {first} to {last}")
    if criteria and not path:
        sys.exit("The criteria file is not in criteria/ any more: --show and --export cannot find this question.")
    arguments = [question] + (["--criteria", path] if path else []) + (["--speaker", speaker] if speaker else [])
    print("Arguments of explore/ask_transcripts.py for this question (add the selection, then --show, and --export DIR):")
    print("  " + shlex.join(arguments + ["--question", str(question_id)]))
