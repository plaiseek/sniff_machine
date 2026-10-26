"""The database: connect() creates it or migrates it to the latest schema (helpers/migrations.py),
and the queries of every group of tables (helpers/tables/) are re-exported here, so that callers
only need

    import helpers.database as db

    with db.connect() as db_conn:
        contents = db.find_contents(db_conn, uploader="Psyhodelik")
"""

import functools
import os
import pathlib
import re
import sqlite3
import sqlite_vec
import threading
import time

from helpers.migrations import MIGRATIONS, V1_COLUMNS
# Re-exported: db.<query> for every query, wherever it is defined
from helpers.tables.contents import *  # noqa: F401, F403
from helpers.tables.diarizations import *  # noqa: F401, F403
from helpers.tables.questions import *  # noqa: F401, F403
from helpers.tables.speakers import *  # noqa: F401, F403
from helpers.tables.transcriptions import *  # noqa: F401, F403

db_path = pathlib.Path("cache/test.db")


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
