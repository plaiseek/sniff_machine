"""Named speakers, their voice samples, and who the diarization labels are.
Import them through helpers.database (import helpers.database as db)."""

import sqlite3

from helpers.tables.diarizations import serialize_embedding


def get_speaker_id(db_conn: sqlite3.Connection, name: str) -> int | None:
    for row in db_conn.execute("SELECT id FROM speakers WHERE name = ?", (name,)):
        return row[0]
    return None


def add_speaker(db_conn: sqlite3.Connection, name: str) -> int:
    """The id of the speaker with this exact name, created if needed."""
    with db_conn:
        db_conn.execute("INSERT INTO speakers (name) VALUES (?) ON CONFLICT (name) DO NOTHING", (name,))
    return get_speaker_id(db_conn, name)


def delete_speaker(db_conn: sqlite3.Connection, name: str) -> bool:
    """Forget a speaker: their samples, sample embeddings and every label naming them go too
    (ON DELETE CASCADE). Returns False if there was no speaker with this name."""
    with db_conn:
        return db_conn.execute("DELETE FROM speakers WHERE name = ?", (name,)).rowcount > 0


def add_speaker_samples(
    db_conn: sqlite3.Connection,
    speaker_id: int,
    content_id: int,
    model: str,
    samples: list[tuple[int, int, list[float] | None]],
) -> int:
    """Store reference ranges of a speaker's voice, (start_ms, end_ms, embedding or None when too
    short), with their `model` embeddings, in one transaction. A range already stored keeps its
    embedding. Returns how many ranges were new."""
    added = 0
    with db_conn:
        for start_ms, end_ms, embedding in samples:
            added += db_conn.execute(
                "INSERT INTO speaker_samples (speaker_id, content_id, start_ms, end_ms) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (speaker_id, content_id, start_ms, end_ms),
            ).rowcount
            db_conn.execute(
                """INSERT OR IGNORE INTO sample_embeddings (sample_id, model, embedding)
SELECT id, ?, ? FROM speaker_samples WHERE speaker_id = ? AND content_id = ? AND start_ms = ? AND end_ms = ?""",
                (model, serialize_embedding(embedding), speaker_id, content_id, start_ms, end_ms),
            )
    return added


def get_speaker_samples(
    db_conn: sqlite3.Connection, model: str
) -> list[tuple[int, str, int, int, int, bytes]]:
    """(speaker id, name, content id, start_ms, end_ms, embedding) of every sample with a `model`
    embedding."""
    return db_conn.execute(
        """SELECT s.id, s.name, p.content_id, p.start_ms, p.end_ms, e.embedding
FROM speakers s JOIN speaker_samples p ON p.speaker_id = s.id
JOIN sample_embeddings e ON e.sample_id = p.id AND e.model = ?
WHERE e.embedding IS NOT NULL ORDER BY s.id, p.content_id, p.start_ms""",
        (model,),
    ).fetchall()


def set_speaker_label(
    db_conn: sqlite3.Connection,
    diarization_id: int,
    speaker: str,
    speaker_id: int | None,
    source: str,
    model: str | None = None,
    similarity: float | None = None,
) -> bool:
    """Say who a label of a diarization is (speaker_id None: nobody known). A 'human' label replaces
    any other; an 'auto' one only replaces an 'auto' one, never a human's. Returns whether it was
    written."""
    query = """INSERT INTO speaker_labels (diarization_id, speaker, speaker_id, source, model, similarity)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (diarization_id, speaker) DO UPDATE SET speaker_id = excluded.speaker_id,
source = excluded.source, model = excluded.model, similarity = excluded.similarity,
updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"""
    if source == "auto":
        query += " WHERE speaker_labels.source = 'auto'"
    elif source != "human":
        raise ValueError(f"source must be 'human' or 'auto', got {source!r}")
    with db_conn:
        cursor = db_conn.execute(query, (diarization_id, speaker, speaker_id, source, model, similarity))
    return cursor.rowcount > 0


def get_speaker_labels(db_conn: sqlite3.Connection, diarization_id: int) -> dict[str, tuple]:
    """{label: (speaker id or None, name or None, source, similarity)} for a diarization."""
    return {
        row[0]: row[1:]
        for row in db_conn.execute(
            """SELECT l.speaker, l.speaker_id, s.name, l.source, l.similarity
FROM speaker_labels l LEFT JOIN speakers s ON s.id = l.speaker_id WHERE l.diarization_id = ?""",
            (diarization_id,),
        )
    }
