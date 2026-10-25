import os
import pathlib
import sqlite3
import sqlite_vec
import threading
import time
from dataclasses import dataclass

db_path = pathlib.Path("cache/test.db")

# The schema, as a list of migrations applied in order by connect(): PRAGMA user_version is the
# number of migrations already applied to a database. Never edit a migration that may have been
# applied somewhere: append a new one. A migration that rebuilds a table must recreate its indexes
# and triggers (views that use it keep working).
MIGRATIONS = [
    # v1: the original schema (does nothing on databases created before migrations existed)
    [
        """CREATE TABLE IF NOT EXISTS contents (
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
)""",
        """CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    content_id INTEGER NOT NULL,
    file_type TEXT NOT NULL CHECK(file_type IN ('ytdlp_info', 'ytdlp_audio', 'ytdlp_subtitles', '16k_mono_wav', 'whisper_result', 'pyannote_result')),
    FOREIGN KEY(content_id) REFERENCES contents(id) ON DELETE CASCADE,
    UNIQUE (content_id, file_type)
)""",
    ],
    # v2: contents where only the identity is mandatory (extractors often leave out uploader,
    # duration or dates: '' and 0 placeholders become NULL), plus channel and series; files without
    # the CHECK on file_type (a new feature only needs a new file type) and with a variant (e.g. a
    # subtitles language) and a creation time. SQLite cannot drop a constraint: both are rebuilt.
    [
        "CREATE TEMP TABLE contents_sequence AS SELECT seq FROM sqlite_sequence WHERE name = 'contents'",
        """CREATE TABLE contents_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,                    -- yt-dlp extractor_key
    external_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    duration_s REAL,
    uploader TEXT,
    channel_id TEXT,                           -- yt-dlp channel_id (or uploader_id): stable channel key
    series TEXT,                               -- programme name, when the extractor gives one
    upload_date TEXT,                          -- ISO 8601 date or datetime
    added_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (platform, external_id),
    UNIQUE (url)
)""",
        """INSERT INTO contents_new (id, platform, external_id, url, title, duration_s, uploader, upload_date, added_at)
SELECT id, platform, external_id, url, NULLIF(title, ''), NULLIF(duration_s, 0), NULLIF(uploader, ''), NULLIF(upload_date, ''), NULL
FROM contents""",
        "DROP TABLE contents",
        "ALTER TABLE contents_new RENAME TO contents",
        # Keep AUTOINCREMENT's high-water mark: ids of deleted contents are never reused
        """UPDATE sqlite_sequence SET seq = MAX(seq, COALESCE((SELECT seq FROM temp.contents_sequence), 0))
WHERE name = 'contents'""",
        "DROP TABLE temp.contents_sequence",
        """CREATE TABLE files_new (
    path TEXT PRIMARY KEY,
    content_id INTEGER NOT NULL,
    file_type TEXT NOT NULL,
    variant TEXT NOT NULL DEFAULT '',          -- '' unless several files of one type exist (e.g. 'fr')
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY(content_id) REFERENCES contents(id) ON DELETE CASCADE,
    UNIQUE (content_id, file_type, variant)
)""",
        # Rows left behind by a content deleted without foreign keys (e.g. from datasette) are dropped
        """INSERT INTO files_new (path, content_id, file_type, created_at)
SELECT path, content_id, file_type, NULL FROM files WHERE content_id IN (SELECT id FROM contents)""",
        "DROP TABLE files",
        "ALTER TABLE files_new RENAME TO files",
    ],
    # v3: diarization runs, their speaker turns, and one voice embedding per turn and model
    [
        """CREATE TABLE diarizations (                -- one row per run: provenance, and 'done' even with 0 turns
    id INTEGER PRIMARY KEY AUTOINCREMENT,      -- never reused: ids of deleted runs cannot point to other contents
    content_id INTEGER NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
    model TEXT NOT NULL,                       -- e.g. 'pyannote/speaker-diarization-community-1'
    params TEXT NOT NULL DEFAULT '{}',         -- json.dumps(params, sort_keys=True)
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (content_id, model, params)
)""",
        """CREATE TABLE turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    diarization_id INTEGER NOT NULL REFERENCES diarizations(id) ON DELETE CASCADE,
    speaker TEXT NOT NULL,                     -- e.g. 'SPEAKER_00': only meaningful inside its diarization
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL CHECK (end_ms > start_ms),
    exclusive INTEGER NOT NULL CHECK (exclusive IN (0, 1)),  -- 1 = from the exclusive diarization
    overlap_ms INTEGER NOT NULL DEFAULT 0      -- overlapped speech inside this turn
)""",
        "CREATE INDEX turns_by_diarization ON turns(diarization_id, exclusive, start_ms)",
        """CREATE TABLE turn_embeddings (
    turn_id INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    model TEXT NOT NULL,                       -- e.g. 'speechbrain/spkrec-ecapa-voxceleb'
    embedding BLOB,                            -- L2-normalised float32 (sqlite_vec.serialize_float32), NULL = too short
    PRIMARY KEY (turn_id, model)
)""",
    ],
]

# The columns migration v2 copies when it rebuilds contents and files
V1_COLUMNS = {
    "contents": ["id", "platform", "external_id", "url", "title", "duration_s", "uploader", "upload_date"],
    "files": ["path", "content_id", "file_type"],
}

# Only one thread of a process migrates (the backup cannot run inside the migration's transaction),
# and once it failed, the other threads of the process do not retry it (nor back up again)
_migration_lock = threading.Lock()
_migration_error = None


def get_schema_version(db_conn: sqlite3.Connection) -> int:
    return db_conn.execute("PRAGMA user_version").fetchone()[0]


def _check_before_migrating(db_conn: sqlite3.Connection, version: int) -> None:
    """Refuse to migrate a damaged database, or rebuilt tables that were changed by hand."""
    # A damaged index could make a table rebuild silently skip rows
    if (problems := db_conn.execute("PRAGMA integrity_check").fetchall()) != [("ok",)]:
        raise RuntimeError(f"{db_path} is damaged, not migrated: {problems[:5]}")
    if version < 2:
        unexpected = []
        for table, columns in V1_COLUMNS.items():
            found = [row[1] for row in db_conn.execute(f"PRAGMA table_info({table})")]
            if found and found != columns:
                unexpected.append(f"{table} columns {found}")
        unexpected += [
            f"{kind} {name}"
            for kind, name in db_conn.execute(
                "SELECT type, name FROM sqlite_master WHERE tbl_name IN ('contents', 'files') AND type IN ('index', 'trigger') AND sql IS NOT NULL"
            )
        ]
        if unexpected:
            raise RuntimeError(
                f"{db_path} not migrated: migration v2 rebuilds contents and files and would drop what was "
                f"added to them by hand: {', '.join(unexpected)}"
            )


def _backup(db_conn: sqlite3.Connection, version: int) -> pathlib.Path:
    backup_dir = db_path.parent / "backups"
    os.makedirs(backup_dir, exist_ok=True)
    name = f"{db_path.stem}-v{version}-{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"
    backup_path, n = backup_dir / f"{name}.db", 1
    while backup_path.exists():
        backup_path, n = backup_dir / f"{name}-{n}.db", n + 1
    # Written under a temporary name, so that an interrupted copy is never taken for a backup
    partial_path = backup_path.with_name(backup_path.name + ".partial")
    try:
        db_conn.execute("VACUUM INTO ?", (str(partial_path),))  # consistent copy, even in WAL mode
        fd = os.open(partial_path, os.O_RDONLY)
        try:
            os.fsync(fd)  # older SQLite versions (e.g. Ubuntu 22.04's 3.37) do not sync VACUUM INTO
        finally:
            os.close(fd)
        os.replace(partial_path, backup_path)
    except BaseException:
        partial_path.unlink(missing_ok=True)
        pathlib.Path(f"{partial_path}-journal").unlink(missing_ok=True)
        raise
    return backup_path


def _foreign_key_violations(db_conn: sqlite3.Connection) -> dict:
    """Number of rows violating a foreign key, per table."""
    counts = {}
    for table, *_ in db_conn.execute("PRAGMA foreign_key_check"):
        counts[table] = counts.get(table, 0) + 1
    return counts


def migrate(db_conn: sqlite3.Connection) -> None:
    """Apply the missing MIGRATIONS in one transaction, after backing up the database."""
    global _migration_error
    if get_schema_version(db_conn) == len(MIGRATIONS):
        return
    with _migration_lock:
        if _migration_error is not None:
            raise RuntimeError(f"The migration of {db_path} failed in this process: {_migration_error!r}")
        try:
            _migrate(db_conn)
        except BaseException as e:
            _migration_error = e
            raise


def _migrate(db_conn: sqlite3.Connection) -> None:
    db_conn.commit()
    # One read snapshot, so that the schema checked is the one of the version read, even if
    # another process is migrating at the same time
    db_conn.execute("BEGIN")
    try:
        version = get_schema_version(db_conn)
        if version == len(MIGRATIONS):
            return  # migrated by another thread or process meanwhile
        if version > len(MIGRATIONS):
            raise RuntimeError(
                f"{db_path} has schema v{version}, newer than this code (v{len(MIGRATIONS)}): update the code"
            )
        has_tables = db_conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] > 0
        if has_tables:
            _check_before_migrating(db_conn, version)
    finally:
        if db_conn.in_transaction:
            db_conn.execute("COMMIT")
    backup_path = _backup(db_conn, version) if has_tables else None

    isolation_level, db_conn.isolation_level = db_conn.isolation_level, None
    # Foreign keys must be off while tables are rebuilt (DROP TABLE would cascade); the pragma is
    # ignored inside a transaction. legacy_alter_table: renaming a rebuilt table does not fail on
    # (and keeps working) the views that use it
    db_conn.execute("PRAGMA foreign_keys = OFF")
    db_conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        db_conn.execute("BEGIN IMMEDIATE")  # another process migrating at the same time waits here
        try:
            version = get_schema_version(db_conn)  # re-read under the lock
            # Rows that already violate a foreign key (e.g. a content deleted from datasette or the
            # sqlite3 CLI, without foreign keys) do not block the migration: only new ones do
            violations = _foreign_key_violations(db_conn)
            for v, statements in enumerate(MIGRATIONS[version:], start=version + 1):
                for statement in statements:
                    db_conn.execute(statement)
                db_conn.execute(f"PRAGMA user_version = {v}")
            new_violations = {
                table: n
                for table, n in _foreign_key_violations(db_conn).items()
                if n > violations.get(table, 0)
            }
            if new_violations:
                raise RuntimeError(f"Foreign key violations introduced by the migration: {new_violations}")
            db_conn.execute("COMMIT")
        except BaseException:
            if db_conn.in_transaction:  # SQLite may have rolled back by itself (e.g. disk full)
                db_conn.execute("ROLLBACK")  # user_version is part of the transaction: all or nothing
            raise
    finally:
        db_conn.execute("PRAGMA legacy_alter_table = OFF")
        db_conn.execute("PRAGMA foreign_keys = ON")
        db_conn.isolation_level = isolation_level
    if version == len(MIGRATIONS):  # migrated by another process meanwhile: its backup is enough
        if backup_path is not None:
            backup_path.unlink(missing_ok=True)
    elif backup_path is None:
        print(f"Created {db_path} (schema v{len(MIGRATIONS)})")
    else:
        print(f"Migrated {db_path} from schema v{version} to v{len(MIGRATIONS)} (backup: {backup_path})")


def create_tables(db_conn: sqlite3.Connection) -> None:
    """Kept for older scripts: connect() already creates or migrates the schema."""
    migrate(db_conn)


def connect():
    """Connection to the database, created or migrated to the latest schema if needed."""
    os.makedirs(pathlib.Path(db_path).parent, exist_ok=True)
    # timeout: wait for another script's write (or migration) instead of failing after 5 s
    db_conn = sqlite3.connect(str(db_path), timeout=30)
    db_conn.enable_load_extension(True)
    sqlite_vec.load(db_conn)
    db_conn.enable_load_extension(False)
    db_conn.execute("PRAGMA journal_mode=WAL;")
    db_conn.execute("PRAGMA foreign_keys = ON;")
    migrate(db_conn)
    return db_conn


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
    cursor.execute(
        "INSERT INTO contents (platform, external_id, url, title, duration_s, uploader, upload_date, channel_id, series) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (platform, external_id, url, title, duration_s, uploader, upload_date, channel_id, series),
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


def get_content_from_external_id(
    db_conn: sqlite3.Connection, platform: str, external_id: str
) -> Content | None:
    for row in db_conn.execute(
        """SELECT id, platform, external_id, url, title, duration_s, uploader, upload_date FROM contents WHERE platform = ? AND external_id = ?""",
        (platform, external_id),
    ):
        return Content(*row)
    return None


def get_contents_without_file(
    db_conn: sqlite3.Connection, file_type: str, source_file_types: list[str]
) -> list[Content]:
    """Contents with a file of one of `source_file_types` registered, but no `file_type` one.

    Only the registrations are checked, not whether the files are still on disk."""
    placeholders = ", ".join("?" for _ in source_file_types)
    return [
        Content(*row)
        for row in db_conn.execute(
            f"""SELECT id, platform, external_id, url, title, duration_s, uploader, upload_date FROM contents
WHERE EXISTS (SELECT 1 FROM files WHERE files.content_id = contents.id AND files.file_type IN ({placeholders}))
AND NOT EXISTS (SELECT 1 FROM files WHERE files.content_id = contents.id AND files.file_type = ?)
ORDER BY id""",
            (*source_file_types, file_type),
        )
    ]


def add_content_file(
    db_conn: sqlite3.Connection,
    path: pathlib.Path,
    content_id: int,
    file_type: str,
) -> None:
    # REPLACE: re-registering a recomputed file (force=True, file deleted from disk) updates the row
    db_conn.execute(
        "INSERT OR REPLACE INTO files (path, content_id, file_type) VALUES (?, ?, ?)",
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
    return None


def get_existing_content_file(
    db_conn: sqlite3.Connection, content_id: int, file_type: str
) -> pathlib.Path | None:
    """Registered path of a content file, or None if it is not registered or no longer on disk."""
    path = get_content_file_path(db_conn, content_id, file_type)
    if path is not None and path.is_file():
        return path
    return None
