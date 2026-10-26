"""The contents (videos and audios) and their files.
Import them through helpers.database (import helpers.database as db)."""

import os
import pathlib
import sqlite3
import threading
from dataclasses import dataclass


@dataclass
class Content:
    id: int
    platform: str
    external_id: str
    url: str
    title: str | None
    duration_s: float | None
    uploader: str | None
    upload_date: str | None
    channel_id: str | None = None
    series: str | None = None


# The columns of Content, in order: SELECT them explicitly (a migration may add columns to contents)
CONTENT_COLUMNS = "id, platform, external_id, url, title, duration_s, uploader, upload_date, channel_id, series"


def add_content(
    db_conn: sqlite3.Connection,
    platform: str,
    external_id: str,
    url: str,
    title: str | None,
    duration_s: float | None,
    uploader: str | None,
    upload_date: str | None,
    channel_id: str | None = None,
    series: str | None = None,
) -> Content:
    cursor = db_conn.cursor()
    with db_conn:  # commits, or rolls back (e.g. another script added the same content meanwhile)
        cursor.execute(
            "INSERT INTO contents (platform, external_id, url, title, duration_s, uploader, upload_date, channel_id, series) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (platform, external_id, url, title, duration_s, uploader, upload_date, channel_id, series),
        )
    content = Content(
        int(cursor.lastrowid),
        platform,
        external_id,
        url,
        title,
        duration_s,
        uploader,
        upload_date,
        channel_id,
        series,
    )
    return content


def get_content(db_conn: sqlite3.Connection, content_id: int) -> Content | None:
    for row in db_conn.execute(
        f"SELECT {CONTENT_COLUMNS} FROM contents WHERE id = ?", (content_id,)
    ):
        return Content(*row)
    return None


def get_content_from_url(
    db_conn: sqlite3.Connection, content_url: str
) -> Content | None:
    for row in db_conn.execute(
        f"SELECT {CONTENT_COLUMNS} FROM contents WHERE url = ?",
        (content_url,),
    ):
        return Content(*row)
    return None


def get_content_from_external_id(
    db_conn: sqlite3.Connection, platform: str, external_id: str
) -> Content | None:
    for row in db_conn.execute(
        f"SELECT {CONTENT_COLUMNS} FROM contents WHERE platform = ? AND external_id = ?",
        (platform, external_id),
    ):
        return Content(*row)
    return None


def set_content_channel(
    db_conn: sqlite3.Connection, content_id: int, channel_id: str | None, series: str | None
) -> None:
    """Fill channel_id and series where they are still NULL (contents added before schema v2)."""
    with db_conn:
        db_conn.execute(
            "UPDATE contents SET channel_id = COALESCE(channel_id, ?), series = COALESCE(series, ?) WHERE id = ?",
            (channel_id, series, content_id),
        )


def get_contents_without_file(
    db_conn: sqlite3.Connection, file_type: str, source_file_types: list[str]
) -> list[Content]:
    """Contents with a file of one of `source_file_types` registered, but no `file_type` one.

    Only the registrations are checked, not whether the files are still on disk."""
    placeholders = ", ".join("?" for _ in source_file_types)
    return [
        Content(*row)
        for row in db_conn.execute(
            f"""SELECT {CONTENT_COLUMNS} FROM contents
WHERE EXISTS (SELECT 1 FROM files WHERE files.content_id = contents.id AND files.file_type IN ({placeholders}))
AND NOT EXISTS (SELECT 1 FROM files WHERE files.content_id = contents.id AND files.file_type = ?)
ORDER BY id""",
            (*source_file_types, file_type),
        )
    ]


def get_contents_with_file(
    db_conn: sqlite3.Connection, file_types: list[str]
) -> list[Content]:
    """Contents with a file of one of `file_types` registered (not checked on disk), by id."""
    placeholders = ", ".join("?" for _ in file_types)
    return [
        Content(*row)
        for row in db_conn.execute(
            f"""SELECT {CONTENT_COLUMNS} FROM contents
WHERE EXISTS (SELECT 1 FROM files WHERE files.content_id = contents.id AND files.file_type IN ({placeholders}))
ORDER BY id""",
            file_types,
        )
    ]


def add_content_file(
    db_conn: sqlite3.Connection,
    path: pathlib.Path,
    content_id: int,
    file_type: str,
    variant: str = "",
) -> None:
    # REPLACE: re-registering a recomputed file (force=True, file deleted from disk) updates the row
    with db_conn:  # commits, or rolls back: a failed write never keeps the database locked
        db_conn.execute(
            "INSERT OR REPLACE INTO files (path, content_id, file_type, variant) VALUES (?, ?, ?, ?)",
            (str(path), content_id, file_type, variant),
        )


def get_content_file_path(
    db_conn: sqlite3.Connection, content_id: int, file_type: str, variant: str = ""
) -> pathlib.Path | None:
    for row in db_conn.execute(
        "SELECT path FROM files WHERE content_id = ? AND file_type = ? AND variant = ?",
        (content_id, file_type, variant),
    ):
        return pathlib.Path(row[0])
    return None


def get_existing_content_file(
    db_conn: sqlite3.Connection, content_id: int, file_type: str, variant: str = ""
) -> pathlib.Path | None:
    """Registered path of a content file, or None if it is not registered or no longer on disk."""
    path = get_content_file_path(db_conn, content_id, file_type, variant)
    if path is not None and path.is_file():
        return path
    return None


def write_atomic(path: pathlib.Path, text: str) -> None:
    """Write a text file so that a crash never leaves a half-written file that looks valid."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # One temporary name per process and thread: two writers never share it
    partial_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.part")
    with open(partial_path, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(partial_path, path)


def find_contents(
    db_conn: sqlite3.Connection,
    ids: list[int] | None = None,
    platform: str | None = None,
    channel_id: str | None = None,
    uploader: str | None = None,
    title: str | None = None,
    uploaded_from: str | None = None,
    uploaded_to: str | None = None,
) -> list[Content]:
    """Contents matching every given filter, oldest upload first. `uploader` and `title` match a
    substring, ignoring case (accented letters included); dates are inclusive YYYY-MM-DD bounds on
    the (UTC) upload date, and contents with no known upload date are left out as soon as one bound
    is given."""
    where, args = [], []
    if ids is not None:
        where.append(f"id IN ({', '.join('?' for _ in ids)})")
        args += ids
    if platform is not None:
        where.append("platform = ?")
        args.append(platform)
    if channel_id is not None:
        where.append("channel_id = ?")
        args.append(channel_id)
    if uploader is not None:
        where.append("instr(casefold(uploader), casefold(?)) > 0")
        args.append(uploader)
    if title is not None:
        where.append("instr(casefold(title), casefold(?)) > 0")
        args.append(title)
    if uploaded_from is not None:
        where.append("substr(upload_date, 1, 10) >= ?")
        args.append(uploaded_from)
    if uploaded_to is not None:
        where.append("substr(upload_date, 1, 10) <= ?")
        args.append(uploaded_to)
    query = f"SELECT {CONTENT_COLUMNS} FROM contents"
    if where:
        query += " WHERE " + " AND ".join(where)
    return [Content(*row) for row in db_conn.execute(query + " ORDER BY upload_date, id", args)]
