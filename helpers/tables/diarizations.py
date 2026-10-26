"""Diarizations, their speaker turns and the turns' voice embeddings.
Import them through helpers.database (import helpers.database as db)."""

import json
import pathlib
import sqlite3
import sqlite_vec
from dataclasses import dataclass

from helpers.tables.contents import write_atomic


def params_key(params: dict | None) -> str:
    """The one text form of model params stored in the database (diarizations.params)."""
    return json.dumps(params or {}, sort_keys=True)


@dataclass
class Turn:
    id: int
    diarization_id: int
    speaker: str
    start_ms: int
    end_ms: int
    exclusive: int
    overlap_ms: int


def get_diarization_id(
    db_conn: sqlite3.Connection, content_id: int, model: str, params: dict | None = None
) -> int | None:
    for row in db_conn.execute(
        "SELECT id FROM diarizations WHERE content_id = ? AND model = ? AND params = ?",
        (content_id, model, params_key(params)),
    ):
        return row[0]
    return None


def add_diarization(
    db_conn: sqlite3.Connection,
    content_id: int,
    model: str,
    params: dict | None,
    turns: list[tuple[str, int, int, int, int]],
    result_text: str | None = None,
    result_path: pathlib.Path | None = None,
) -> int | None:
    """Store a diarization run and all its turns, (speaker, start_ms, end_ms, exclusive, overlap_ms),
    in one transaction, with the server's raw answer (`result_text`) written to `result_path` and
    registered as its 'pyannote_result' file (variant = the diarization id). Returns the new
    diarization id, or None if this (content, model, params) was already stored (e.g. by another
    script meanwhile): nothing is written then."""
    with db_conn:  # commits, or rolls back on any error: never a diarization without its turns
        cursor = db_conn.execute(
            "INSERT INTO diarizations (content_id, model, params) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
            (content_id, model, params_key(params)),
        )
        if cursor.rowcount == 0:
            return None
        diarization_id = int(cursor.lastrowid)
        db_conn.executemany(
            "INSERT INTO turns (diarization_id, speaker, start_ms, end_ms, exclusive, overlap_ms) VALUES (?, ?, ?, ?, ?, ?)",
            [(diarization_id, *turn) for turn in turns],
        )
        if result_text is not None and result_path is not None:
            path = result_path.with_name(f"{result_path.stem}_{diarization_id}{result_path.suffix}")
            write_atomic(path, result_text)
            db_conn.execute(
                "INSERT OR REPLACE INTO files (path, content_id, file_type, variant) VALUES (?, ?, ?, ?)",
                (str(path), content_id, "pyannote_result", str(diarization_id)),
            )
    return diarization_id


def get_turns(
    db_conn: sqlite3.Connection, diarization_id: int, exclusive: bool | None = None
) -> list[Turn]:
    """Turns of a diarization by start time: the exclusive ones (at most one speaker at a time),
    the regular ones (with overlapping speech), or both (exclusive=None)."""
    query = "SELECT id, diarization_id, speaker, start_ms, end_ms, exclusive, overlap_ms FROM turns WHERE diarization_id = ?"
    args = [diarization_id]
    if exclusive is not None:
        query += " AND exclusive = ?"
        args.append(int(exclusive))
    return [Turn(*row) for row in db_conn.execute(query + " ORDER BY start_ms, id", args)]


def delete_diarization(db_conn: sqlite3.Connection, diarization_id: int, force: bool = False) -> None:
    """Delete a diarization run: its turns, their embeddings and its speaker labels go with it
    (ON DELETE CASCADE), and so does the registration of its raw result file (the file itself is
    kept). Labels set by a human cannot be recomputed: without force, they make it fail."""
    if not force and db_conn.execute(
        "SELECT 1 FROM speaker_labels WHERE diarization_id = ? AND source = 'human'", (diarization_id,)
    ).fetchone():
        raise ValueError(
            f"Diarization {diarization_id} has speaker labels set by a human: pass force=True to delete them too"
        )
    with db_conn:
        db_conn.execute(
            "DELETE FROM files WHERE file_type = 'pyannote_result' AND variant = ? AND content_id = (SELECT content_id FROM diarizations WHERE id = ?)",
            (str(diarization_id), diarization_id),
        )
        db_conn.execute("DELETE FROM diarizations WHERE id = ?", (diarization_id,))


def serialize_embedding(embedding: list[float] | None) -> bytes | None:
    return None if embedding is None else sqlite_vec.serialize_float32(embedding)


def get_unembedded_turns(db_conn: sqlite3.Connection, content_id: int, model: str) -> list[Turn]:
    """Exclusive turns of every diarization of a content that have no embedding row for `model`
    (a turn too short to embed has a row with a NULL embedding), by start time."""
    return [
        Turn(*row)
        for row in db_conn.execute(
            """SELECT t.id, t.diarization_id, t.speaker, t.start_ms, t.end_ms, t.exclusive, t.overlap_ms
FROM turns t JOIN diarizations d ON d.id = t.diarization_id
WHERE d.content_id = ? AND t.exclusive = 1
AND NOT EXISTS (SELECT 1 FROM turn_embeddings e WHERE e.turn_id = t.id AND e.model = ?)
ORDER BY t.start_ms, t.id""",
            (content_id, model),
        )
    ]


def add_turn_embeddings(
    db_conn: sqlite3.Connection, model: str, embeddings: list[tuple[int, list[float] | None]]
) -> int:
    """Store (turn id, embedding or None when the turn is too short) pairs in one transaction.
    Embeddings already stored (e.g. by another script meanwhile) are kept. Returns how many rows
    were added."""
    with db_conn:
        cursor = db_conn.executemany(
            "INSERT OR IGNORE INTO turn_embeddings (turn_id, model, embedding) VALUES (?, ?, ?)",
            [(turn_id, model, serialize_embedding(embedding)) for turn_id, embedding in embeddings],
        )
    return cursor.rowcount


def get_turn_embeddings(
    db_conn: sqlite3.Connection, diarization_id: int, model: str
) -> list[tuple[Turn, bytes | None]]:
    """The exclusive turns of a diarization with their `model` embedding (None when not computed or
    too short), by start time."""
    return [
        (Turn(*row[:7]), row[7])
        for row in db_conn.execute(
            """SELECT t.id, t.diarization_id, t.speaker, t.start_ms, t.end_ms, t.exclusive, t.overlap_ms, e.embedding
FROM turns t LEFT JOIN turn_embeddings e ON e.turn_id = t.id AND e.model = ?
WHERE t.diarization_id = ? AND t.exclusive = 1 ORDER BY t.start_ms, t.id""",
            (model, diarization_id),
        )
    ]


def get_turn_embedding_models(db_conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in db_conn.execute("SELECT DISTINCT model FROM turn_embeddings ORDER BY model")]
