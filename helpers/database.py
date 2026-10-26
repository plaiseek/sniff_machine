import functools
import json
import os
import pathlib
import re
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
    # v4: named speakers, the reference ranges of their voice (enrollment, kept across
    # re-diarizations and model changes), and who each diarization label is. Deleting a speaker
    # deletes everything derived from their voice (cascades).
    [
        """CREATE TABLE speakers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)""",
        """CREATE TABLE speaker_samples (               -- a range of a content where only this speaker talks
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    speaker_id INTEGER NOT NULL REFERENCES speakers(id) ON DELETE CASCADE,
    content_id INTEGER NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL CHECK (end_ms > start_ms),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (speaker_id, content_id, start_ms, end_ms)
)""",
        """CREATE TABLE sample_embeddings (             -- like turn_embeddings: one per sample and model
    sample_id INTEGER NOT NULL REFERENCES speaker_samples(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    embedding BLOB,                            -- L2-normalised float32, NULL = too short
    PRIMARY KEY (sample_id, model)
)""",
        """CREATE TABLE speaker_labels (                -- who a label of a diarization is
    diarization_id INTEGER NOT NULL REFERENCES diarizations(id) ON DELETE CASCADE,
    speaker TEXT NOT NULL,                     -- the diarization label, e.g. 'SPEAKER_01'
    speaker_id INTEGER REFERENCES speakers(id) ON DELETE CASCADE,  -- NULL = nobody known
    source TEXT NOT NULL CHECK (source IN ('human', 'auto')),      -- 'auto' never replaces 'human'
    model TEXT,                                -- 'auto': the embedding model compared
    similarity REAL,                           -- 'auto': cosine similarity to the best speaker
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (diarization_id, speaker)
)""",
        "CREATE INDEX speaker_samples_by_content ON speaker_samples(content_id)",
        "CREATE INDEX speaker_labels_by_speaker ON speaker_labels(speaker_id)",
    ],
    # v5: the words of each content's transcription, and the speaker turn each one falls in
    [
        """CREATE TABLE transcriptions (               -- the words of a whisper_result file were stored: 'done' even with 0 words
    content_id INTEGER PRIMARY KEY REFERENCES contents(id) ON DELETE CASCADE,
    path TEXT NOT NULL,                        -- the whisper_result file they come from
    language TEXT,                             -- as Whisper reports it, e.g. 'french'
    diarization_id INTEGER REFERENCES diarizations(id) ON DELETE SET NULL,  -- words.turn_id are its turns; NULL = none
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)""",
        """CREATE TABLE words (
    content_id INTEGER NOT NULL REFERENCES transcriptions(content_id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,                      -- position in the transcription, from 0
    text TEXT NOT NULL,                        -- as Whisper wrote it: 'l''histoire', 'compte.', '?'
    norm TEXT NOT NULL,                        -- casefolded, without the punctuation around it: 'compte'; '' for '?'
    start_ms INTEGER NOT NULL,                 -- DTW time of its first token
    end_ms INTEGER NOT NULL,                   -- DTW time of its last token: often = start_ms, sometimes before it
    prob REAL,                                 -- product of its token probabilities
    turn_id INTEGER REFERENCES turns(id) ON DELETE SET NULL,  -- the exclusive turn its middle falls in, NULL = none
    PRIMARY KEY (content_id, idx)
) WITHOUT ROWID""",
        "CREATE INDEX words_by_norm ON words(norm)",
        "CREATE INDEX words_by_turn ON words(turn_id)",  # also makes deleting a diarization fast (SET NULL)
    ],
    # v6: the sentences of the transcriptions, never across two speakers, with a full-text index
    [
        """CREATE TABLE sentences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id INTEGER NOT NULL REFERENCES transcriptions(content_id) ON DELETE CASCADE,
    first_idx INTEGER NOT NULL,                -- its words: words.idx from first_idx to last_idx
    last_idx INTEGER NOT NULL CHECK (last_idx >= first_idx),
    start_ms INTEGER NOT NULL,                 -- the earliest time of its words
    end_ms INTEGER NOT NULL,                   -- the latest
    diarization_id INTEGER REFERENCES diarizations(id) ON DELETE CASCADE,  -- = transcriptions.diarization_id
    speaker TEXT,                              -- the label of its words' turns in that diarization, NULL = unknown
    text TEXT NOT NULL,                        -- its words joined with spaces
    splitter TEXT NOT NULL,                    -- the wtpsplit model (e.g. 'sat-12l-sm'), or 'punctuation'
    UNIQUE (content_id, first_idx)
)""",
        # Search: sentences_fts MATCH 'pete*' finds péter, pété, Pète... (case and accents ignored)
        """CREATE VIRTUAL TABLE sentences_fts USING fts5(
    text, content='sentences', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
)""",
        """CREATE TRIGGER sentences_fts_insert AFTER INSERT ON sentences BEGIN
    INSERT INTO sentences_fts (rowid, text) VALUES (new.id, new.text);
END""",
        """CREATE TRIGGER sentences_fts_delete AFTER DELETE ON sentences BEGIN
    INSERT INTO sentences_fts (sentences_fts, rowid, text) VALUES ('delete', old.id, old.text);
END""",
        """CREATE TRIGGER sentences_fts_update AFTER UPDATE ON sentences BEGIN
    INSERT INTO sentences_fts (sentences_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO sentences_fts (rowid, text) VALUES (new.id, new.text);
END""",
        "CREATE INDEX sentences_by_diarization ON sentences(diarization_id)",
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
    # casefold(text): case-insensitive comparisons that also fold accented letters (LIKE only folds ASCII)
    db_conn.create_function("casefold", 1, _casefold, deterministic=True)
    # text REGEXP pattern: Python's re.search, e.g. words.norm REGEXP '^pipis?$'
    db_conn.create_function("regexp", 2, _regexp, deterministic=True)
    migrate(db_conn)
    return db_conn


def _casefold(text):
    return text.casefold() if isinstance(text, str) else text


@functools.lru_cache(maxsize=64)
def _compiled(pattern: str) -> re.Pattern:
    return re.compile(pattern)


def _regexp(pattern, text):
    return text is not None and _compiled(pattern).search(text) is not None


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
