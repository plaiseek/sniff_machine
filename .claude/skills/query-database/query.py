"""Run one read-only SQL query on the database and print the result as a table.

    python3.11 .claude/skills/query-database/query.py "SELECT id, title FROM contents WHERE uploader LIKE ?" "%name%"
    python3.11 .claude/skills/query-database/query.py --schema

The database is opened read-only: a query that would change it fails with
"attempt to write a readonly database". At most --limit rows are printed (default 100).
"""

import argparse
import pathlib
import re
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "cache" / "test.db"

parser = argparse.ArgumentParser()
parser.add_argument("sql", nargs="?", help="one SQL statement, with ? placeholders for the params")
parser.add_argument("params", nargs="*", help="values for the ? placeholders, in order")
parser.add_argument("--schema", action="store_true", help="print the tables and their columns")
parser.add_argument("--limit", type=int, default=100)
args = parser.parse_args()

if not DB_PATH.is_file():
    sys.exit(f"No database at {DB_PATH}")
db_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
# casefold(text): lower case that also folds accented letters (LIKE only ignores the case of A-Z)
db_conn.create_function("casefold", 1, lambda t: t.casefold() if isinstance(t, str) else t, deterministic=True)
# text REGEXP pattern: Python's re.search (e.g. words.norm REGEXP '^pipis?$')
db_conn.create_function("regexp", 2, lambda p, t: t is not None and re.search(p, t) is not None, deterministic=True)

if args.schema:
    for (sql,) in db_conn.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type DESC, name"
    ):
        print(sql + ";\n")
    sys.exit(0)
if not args.sql:
    parser.error("give an SQL query, or --schema")

def value(text: str):
    """A number when the text is one (an SQL comparison of a computed number with a text value
    is always false: `HAVING COUNT(*) >= '2'` matches nothing), else the text itself."""
    for kind in (int, float):
        try:
            number = kind(text)
        except ValueError:
            continue
        if str(number) == text:  # canonical only: '0123' or '1e3' stay texts
            return number
    return text


try:
    cursor = db_conn.execute(args.sql, [value(p) for p in args.params])
except sqlite3.Error as e:
    sys.exit(f"SQL error: {e}")
columns = [d[0] for d in cursor.description or []]
rows = cursor.fetchmany(args.limit + 1)
more = len(rows) > args.limit
rows = rows[: args.limit]


def cell(value):
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    text = "NULL" if value is None else str(value)
    return text if len(text) <= 80 else text[:77] + "..."


table = [columns] + [[cell(v) for v in row] for row in rows]
widths = [max(len(r[i]) for r in table) for i in range(len(columns))]
for i, r in enumerate(table):
    print("  ".join(v.ljust(w) for v, w in zip(r, widths)).rstrip())
    if i == 0:
        print("  ".join("-" * w for w in widths))
print(f"({len(rows)} rows{', more not shown: use --limit or a narrower query' if more else ''})")
