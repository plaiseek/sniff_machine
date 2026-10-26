"""Questions asked to an LLM about the transcriptions, and what it found (explore/ask_transcripts.py).
Import them through helpers.database (import helpers.database as db)."""

import sqlite3

from helpers.tables.diarizations import params_key
from helpers.tables.transcriptions import get_transcription_state


def add_question(db_conn: sqlite3.Connection, text: str, speaker: str | None, model: str, settings: dict) -> int:
    """The id of a question asked with this model and settings, added if new. speaker: only the
    sentences of the labels named so can match (None: anyone's)."""
    key = (text, speaker or "", model, params_key(settings))
    with db_conn:
        # Not INSERT OR IGNORE: it would use up an id each time the question is asked again
        db_conn.execute(
            """INSERT INTO questions (text, speaker, model, settings) SELECT ?, ?, ?, ?
WHERE NOT EXISTS (SELECT 1 FROM questions WHERE text = ? AND speaker = ? AND model = ? AND settings = ?)""",
            key + key,
        )
    return db_conn.execute(
        "SELECT id FROM questions WHERE text = ? AND speaker = ? AND model = ? AND settings = ?", key
    ).fetchone()[0]


def find_questions(db_conn: sqlite3.Connection, text: str, speaker: str | None) -> list[tuple]:
    """(id, model, settings, created_at) of the questions with this text and speaker, newest first."""
    return db_conn.execute(
        "SELECT id, model, settings, created_at FROM questions WHERE text = ? AND speaker = ? ORDER BY id DESC",
        (text, speaker or ""),
    ).fetchall()


def question_done(db_conn: sqlite3.Connection, question_id: int, content_id: int) -> bool:
    """Whether the question was asked on the words of a content as they are aligned now (a new
    diarization changes who says them)."""
    row = db_conn.execute(
        """SELECT q.diarization_id IS t.diarization_id FROM question_contents q
JOIN transcriptions t ON t.content_id = q.content_id WHERE q.question_id = ? AND q.content_id = ?""",
        (question_id, content_id),
    ).fetchone()
    return bool(row and row[0])


def replace_findings(
    db_conn: sqlite3.Connection,
    question_id: int,
    content_id: int,
    state: tuple,
    windows: int,
    candidates: int,
    tokens: tuple[int, int],
    findings: list[tuple],
) -> None:
    """Store what a question found in a content, read from its words as they were in `state`
    (get_transcription_state()), in place of what it found before, in one transaction: (first_idx,
    last_idx, start_ms, end_ms, speakers, verdict, confidence, attribution, quote, quote_found,
    explanation, screening) rows, and the (prompt, completion) tokens spent. Raises RuntimeError if
    the words changed meanwhile: ask again."""
    with db_conn:
        # The findings go with it (cascade); writing first takes the write lock before the check
        db_conn.execute("DELETE FROM question_contents WHERE question_id = ? AND content_id = ?", (question_id, content_id))
        if get_transcription_state(db_conn, content_id) != tuple(state):
            raise RuntimeError(f"The words of content {content_id} changed while the question was asked: ask again")
        db_conn.execute(
            """INSERT INTO question_contents (question_id, content_id, diarization_id, windows, candidates, prompt_tokens, completion_tokens)
VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (question_id, content_id, state[1], windows, candidates, *tokens),
        )
        db_conn.executemany(
            """INSERT INTO findings (question_id, content_id, first_idx, last_idx, start_ms, end_ms, speakers, verdict, confidence,
attribution, quote, quote_found, explanation, screening) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(question_id, content_id, *finding) for finding in findings],
        )


def get_findings(db_conn: sqlite3.Connection, question_id: int, content_ids: list[int] | None = None, verdicts=("yes", "unsure")) -> list[tuple]:
    """(content_id, diarization_id, first_idx, last_idx, start_ms, end_ms, speakers, verdict, confidence,
    attribution, quote, quote_found, explanation, screening) of what a question found, oldest
    content first. diarization_id: the one whose labels `speakers` are (None: none, or deleted since)."""
    query = f"""SELECT f.content_id, q.diarization_id, f.first_idx, f.last_idx, f.start_ms, f.end_ms, f.speakers, f.verdict,
f.confidence, f.attribution, f.quote, f.quote_found, f.explanation, f.screening
FROM findings f JOIN question_contents q ON q.question_id = f.question_id AND q.content_id = f.content_id
JOIN contents c ON c.id = f.content_id
WHERE f.question_id = ? AND f.verdict IN ({', '.join('?' for _ in verdicts)})"""
    rows = db_conn.execute(query + " ORDER BY c.upload_date, c.id, f.start_ms", (question_id, *verdicts)).fetchall()
    wanted = None if content_ids is None else set(content_ids)
    return [row for row in rows if wanted is None or row[0] in wanted]
