import os
import pathlib
import sqlite3
import sqlite_vec
from dataclasses import dataclass

db_path = pathlib.Path("cache/test.db")

def connect():
    os.makedirs(pathlib.Path(db_path).parent, exist_ok=True)
    db_conn = sqlite3.connect(str(db_path))
    db_conn.enable_load_extension(True)
    sqlite_vec.load(db_conn)
    db_conn.enable_load_extension(False)
    db_conn.execute("PRAGMA journal_mode=WAL;")
    db_conn.execute("PRAGMA foreign_keys = ON;")
    return db_conn


def create_tables(db_conn: sqlite3.Connection) -> None:
    cursor = db_conn.cursor()
    cursor.execute("""
CREATE TABLE IF NOT EXISTS contents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    duration_s INTEGER NOT NULL,
    uploader TEXT NOT NULL,
    upload_date TEXT NOT NULL,
    UNIQUE (platform, external_id),
    UNIQUE (url)
)
    """)
    cursor.execute("""
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    content_id INTEGER NOT NULL,
    file_type TEXT NOT NULL CHECK(file_type IN ('ytdlp_info', 'mp3_audio', 'srt_subtitles', 'whisper_result', 'transcription')),
    FOREIGN KEY(content_id) REFERENCES contents(id) ON DELETE CASCADE,
    UNIQUE (content_id, file_type)
)
    """)
    db_conn.commit()
    cursor.close()


@dataclass
class Content:
    id: int
    platform: str
    external_id: str
    url: str
    title: str
    duration_s: int
    uploader: str
    upload_date: str


def add_content(
    db_conn: sqlite3.Connection,
    platform: str,
    external_id: str,
    url: str,
    title: str,
    duration_s: int,
    uploader: str,
    upload_date: str,
) -> Content:
    cursor = db_conn.cursor()
    cursor.execute(
        "INSERT INTO contents (platform, external_id, url, title, duration_s, uploader, upload_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (platform, external_id, url, title, duration_s, uploader, upload_date),
    )
    db_conn.commit()
    content = Content(
        int(cursor.lastrowid),
        platform,
        external_id,
        url,
        title,
        duration_s,
        uploader,
        upload_date,
    )
    return content


def get_content_from_url(
    db_conn: sqlite3.Connection, content_url: str
) -> Content | None:
    for row in db_conn.execute(
        """SELECT id, platform, external_id, url, title, duration_s, uploader, upload_date FROM contents WHERE url = ?""",
        (content_url,),
    ):
        return Content(*row)
    return None


def add_content_file(
    db_conn: sqlite3.Connection,
    path: pathlib.Path,
    content_id: int,
    file_type: str,
) -> None:
    db_conn.execute(
        "INSERT INTO files (path, content_id, file_type) VALUES (?, ?, ?)",
        (str(path), content_id, file_type),
    )
    db_conn.commit()


def get_content_file_path(
    db_conn: sqlite3.Connection, content_id: int, file_type: str
) -> pathlib.Path | None:
    for row in db_conn.execute(
        "SELECT path FROM files WHERE content_id = ? AND file_type = ?",
        (content_id, file_type),
    ):
        return pathlib.Path(row[0])
