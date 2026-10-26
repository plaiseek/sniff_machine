"""The schema migrations, on databases of their own (never cache/test.db).

    python3.11 -m unittest discover tests
"""

import contextlib
import io
import pathlib
import sqlite3
import tempfile
import unittest

import helpers.database as db


class MigrationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_path = db.db_path
        db.db_path = pathlib.Path(self.tmp.name) / "test.db"

    def tearDown(self):
        db.db_path = self.saved_path
        self.tmp.cleanup()

    def check_latest(self, db_conn):
        self.assertEqual(db.get_schema_version(db_conn), len(db.MIGRATIONS))
        self.assertEqual(db_conn.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
        self.assertEqual(db_conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_a_new_database_gets_the_latest_schema(self):
        with contextlib.redirect_stdout(io.StringIO()), db.connect() as db_conn:
            self.check_latest(db_conn)

    def test_a_v1_database_is_backed_up_then_migrated_with_its_rows(self):
        old = sqlite3.connect(db.db_path)
        for statement in db.MIGRATIONS[0]:
            old.execute(statement)
        old.execute(
            "INSERT INTO contents (platform, external_id, url, title, duration_s, uploader, upload_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Youtube", "abc", "https://www.youtube.com/watch?v=abc", "Title", 60, "Someone", "20260101"),
        )
        old.execute("INSERT INTO files (path, content_id, file_type) VALUES ('cache/ytdlp_info/abc.json', 1, 'ytdlp_info')")
        old.execute("PRAGMA user_version = 1")
        old.commit()
        old.close()
        with contextlib.redirect_stdout(io.StringIO()), db.connect() as db_conn:
            self.check_latest(db_conn)
            [content] = db.find_contents(db_conn)
            self.assertEqual((content.external_id, content.title), ("abc", "Title"))
            self.assertEqual(db.get_content_file_path(db_conn, content.id, "ytdlp_info"), pathlib.Path("cache/ytdlp_info/abc.json"))
        backups = list((db.db_path.parent / "backups").glob("test-v1-*.db"))
        self.assertEqual(len(backups), 1)


if __name__ == "__main__":
    unittest.main()
