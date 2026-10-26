"""The stored words of the transcriptions, aligned to the speaker turns, and their sentences.
Import them through helpers.database (import helpers.database as db)."""

import pathlib
import sqlite3
from dataclasses import dataclass


@dataclass
class Word:
    content_id: int
    idx: int
    text: str
    norm: str
    start_ms: int
    end_ms: int
    prob: float | None
    turn_id: int | None


def add_transcription(
    db_conn: sqlite3.Connection,
    content_id: int,
    path: pathlib.Path,
    language: str | None,
    words: list[tuple[str, str, int, int, float | None]],
) -> None:
    """Store the words of a content's transcription result, (text, norm, start_ms, end_ms, prob) in
    order, in one transaction. They replace the words stored before for the content, whose speaker
    turns go with them: align_words() gives the new ones theirs."""
    with db_conn:
        db_conn.execute("DELETE FROM transcriptions WHERE content_id = ?", (content_id,))
        db_conn.execute(
            "INSERT INTO transcriptions (content_id, path, language) VALUES (?, ?, ?)", (content_id, str(path), language)
        )
        db_conn.executemany(
            "INSERT INTO words (content_id, idx, text, norm, start_ms, end_ms, prob) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(content_id, idx, *word) for idx, word in enumerate(words)],
        )


def delete_transcription(db_conn: sqlite3.Connection, content_id: int) -> None:
    """Forget the stored words of a content (e.g. its transcription result was computed again)."""
    with db_conn:
        db_conn.execute("DELETE FROM transcriptions WHERE content_id = ?", (content_id,))


def align_words(db_conn: sqlite3.Connection, content_id: int) -> int | None:
    """Give each stored word of a content the exclusive turn of the content's latest diarization
    that its middle falls in (NULL: in no turn, or no diarization), in one transaction. Returns the
    id of that diarization. The content's sentences, split by speaker, are deleted: split them again."""
    with db_conn:
        db_conn.execute(
            "UPDATE transcriptions SET diarization_id = (SELECT MAX(id) FROM diarizations WHERE content_id = ?) WHERE content_id = ?",
            (content_id, content_id),
        )
        db_conn.execute("DELETE FROM sentences WHERE content_id = ?", (content_id,))
        row = db_conn.execute("SELECT diarization_id FROM transcriptions WHERE content_id = ?", (content_id,)).fetchone()
        diarization_id = row[0] if row else None
        # Exclusive turns never overlap: the one starting last before the middle is the only candidate
        db_conn.execute(
            """UPDATE words SET turn_id = (
    SELECT CASE WHEN (words.start_ms + words.end_ms) / 2.0 < t.end_ms THEN t.id END
    FROM turns t WHERE t.diarization_id = ? AND t.exclusive = 1 AND t.start_ms <= (words.start_ms + words.end_ms) / 2.0
    ORDER BY t.start_ms DESC, t.id DESC LIMIT 1)
WHERE content_id = ?""",
            (diarization_id, content_id),
        )
    return diarization_id


def get_transcription_diarization(db_conn: sqlite3.Connection, content_id: int) -> int | None:
    """The diarization whose turns the stored words of a content are aligned to (None: none)."""
    row = db_conn.execute("SELECT diarization_id FROM transcriptions WHERE content_id = ?", (content_id,)).fetchone()
    return row[0] if row else None


def get_words(db_conn: sqlite3.Connection, content_id: int) -> list[Word]:
    return [
        Word(*row)
        for row in db_conn.execute(
            "SELECT content_id, idx, text, norm, start_ms, end_ms, prob, turn_id FROM words WHERE content_id = ? ORDER BY idx",
            (content_id,),
        )
    ]


def get_words_with_speakers(db_conn: sqlite3.Connection, content_id: int) -> list[tuple[Word, str | None]]:
    """The stored words of a content in order, with the label of their turn (None: no turn)."""
    return [
        (Word(*row[:8]), row[8])
        for row in db_conn.execute(
            """SELECT w.content_id, w.idx, w.text, w.norm, w.start_ms, w.end_ms, w.prob, w.turn_id, t.speaker
FROM words w LEFT JOIN turns t ON t.id = w.turn_id WHERE w.content_id = ? ORDER BY w.idx""",
            (content_id,),
        )
    ]


def get_transcription_state(db_conn: sqlite3.Connection, content_id: int) -> tuple | None:
    """(created_at, diarization_id, number of words) of the stored words of a content, None if there
    are none: what replace_sentences() checks did not change since the words were read."""
    return db_conn.execute(
        """SELECT t.created_at, t.diarization_id, (SELECT COUNT(*) FROM words w WHERE w.content_id = t.content_id)
FROM transcriptions t WHERE t.content_id = ?""",
        (content_id,),
    ).fetchone()


def replace_sentences(
    db_conn: sqlite3.Connection,
    content_id: int,
    state: tuple,
    splitter: str,
    sentences: list[tuple[int, int, int, int, str | None, str]],
) -> None:
    """Store the sentences of a content, (first_idx, last_idx, start_ms, end_ms, speaker, text),
    split by `splitter` from its words as they were in `state` (get_transcription_state()), in place
    of its sentences, in one transaction. Raises RuntimeError if the words changed since (stored or
    aligned again meanwhile, e.g. by another script): split them again."""
    with db_conn:
        db_conn.execute("DELETE FROM sentences WHERE content_id = ?", (content_id,))  # takes the write lock first
        if get_transcription_state(db_conn, content_id) != tuple(state):
            raise RuntimeError(f"The words of content {content_id} changed while they were split: split them again")
        db_conn.executemany(
            """INSERT INTO sentences (content_id, first_idx, last_idx, start_ms, end_ms, diarization_id, speaker, text, splitter)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(content_id, first, last, start, end, state[1], speaker, text, splitter) for first, last, start, end, speaker, text in sentences],
        )


def get_sentences(db_conn: sqlite3.Connection, content_id: int) -> list[tuple]:
    """(first_idx, last_idx, start_ms, end_ms, speaker, text) of the stored sentences of a content, in order."""
    return db_conn.execute(
        "SELECT first_idx, last_idx, start_ms, end_ms, speaker, text FROM sentences WHERE content_id = ? ORDER BY first_idx",
        (content_id,),
    ).fetchall()


def sentences_done(db_conn: sqlite3.Connection, content_id: int, splitter: str | None) -> bool:
    """Whether the sentences of a content were split by `splitter` (None: by any) from its words as
    they are aligned now. True when there is nothing to split: no words, or none stored (see
    helpers.transcripts.update_transcripts())."""
    row = db_conn.execute(
        """SELECT t.diarization_id IS (SELECT MAX(d.id) FROM diarizations d WHERE d.content_id = t.content_id),
EXISTS (SELECT 1 FROM words w WHERE w.content_id = t.content_id),
EXISTS (SELECT 1 FROM sentences s WHERE s.content_id = t.content_id AND (? IS NULL OR s.splitter = ?))
FROM transcriptions t WHERE t.content_id = ?""",
        (splitter, splitter, content_id),
    ).fetchone()
    if row is None:
        return True
    aligned, has_words, split = row
    return bool(aligned and (not has_words or split))


def get_unstored_transcriptions(db_conn: sqlite3.Connection, content_ids: list[int] | None = None) -> list[tuple[int, pathlib.Path]]:
    """(content id, whisper_result path) of the transcription results on disk whose words are not
    stored yet, by content id."""
    rows = db_conn.execute(
        """SELECT f.content_id, f.path FROM files f WHERE f.file_type = 'whisper_result' AND f.variant = ''
AND NOT EXISTS (SELECT 1 FROM transcriptions t WHERE t.content_id = f.content_id) ORDER BY f.content_id"""
    ).fetchall()
    wanted = None if content_ids is None else set(content_ids)
    return [
        (content_id, pathlib.Path(path))
        for content_id, path in rows
        if (wanted is None or content_id in wanted) and pathlib.Path(path).is_file()
    ]


def get_unaligned_transcriptions(db_conn: sqlite3.Connection, content_ids: list[int] | None = None) -> list[int]:
    """Contents whose stored words are not aligned to their latest diarization, by id."""
    rows = db_conn.execute(
        """SELECT t.content_id FROM transcriptions t
WHERE t.diarization_id IS NOT (SELECT MAX(d.id) FROM diarizations d WHERE d.content_id = t.content_id)
ORDER BY t.content_id"""
    ).fetchall()
    wanted = None if content_ids is None else set(content_ids)
    return [content_id for content_id, in rows if wanted is None or content_id in wanted]


def get_words_text(db_conn: sqlite3.Connection, content_id: int, first_idx: int, last_idx: int) -> str:
    """The stored words of a content from first_idx to last_idx, joined with spaces."""
    return " ".join(
        text for text, in db_conn.execute(
            "SELECT text FROM words WHERE content_id = ? AND idx BETWEEN ? AND ? ORDER BY idx", (content_id, first_idx, last_idx)
        )
    )
