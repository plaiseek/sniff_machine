"""Restore cache/test.db from one of its backups (cache/backups/*.db).

    python restore_backup.py                                # list the backups
    python restore_backup.py cache/backups/<backup>.db

Stop every script, job and datasette first. The backup must exist, pass an integrity check and
hold a contents table; the current database is saved in cache/backups/ before being replaced, so
a restore can be undone the same way. Scripts migrate a restored older schema on their next start.
"""

import pathlib
import sqlite3
import sys
import time

import helpers.database as db

backup_dir = db.db_path.parent / "backups"


def describe(path: pathlib.Path) -> str:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        contents = conn.execute("SELECT COUNT(*) FROM contents").fetchone()[0]
        return f"schema v{version}, {contents} contents"
    except sqlite3.Error as e:
        return f"unreadable ({e})"


if len(sys.argv) != 2:
    print(__doc__.strip())
    print(f"\nBackups in {backup_dir}:")
    for path in sorted(backup_dir.glob("*.db")):
        print(f"  {path}  ({describe(path)})")
    sys.exit(0 if len(sys.argv) == 1 else 2)

source = pathlib.Path(sys.argv[1])
if not source.is_file():
    # sqlite3.connect() would create an empty database there, and restoring it would empty test.db
    sys.exit(f"No backup at {source}: run `python restore_backup.py` to list them.")
backup = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
try:
    problems = backup.execute("PRAGMA integrity_check").fetchall()
    has_contents = backup.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'contents'"
    ).fetchone()
except sqlite3.DatabaseError as e:
    sys.exit(f"{source} is not a readable SQLite database: {e}")
if problems != [("ok",)]:
    sys.exit(f"{source} is damaged, not restored: {problems[:5]}")
if not has_contents:
    sys.exit(f"{source} has no contents table: it is not a backup of this database.")

# A plain connection (db.connect() would migrate the database first)
current = sqlite3.connect(str(db.db_path), timeout=30)
if current.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] > 0:
    backup_dir.mkdir(parents=True, exist_ok=True)
    saved = backup_dir / f"{db.db_path.stem}-before-restore-{time.strftime('%Y%m%dT%H%M%S')}.db"
    current.execute("VACUUM INTO ?", (str(saved),))
    print(f"Current database saved as {saved} ({describe(saved)})")
backup.backup(current)  # page by page, consistent even with a -wal file next to test.db
print(f"Restored {source} into {db.db_path} ({describe(db.db_path)}).")
