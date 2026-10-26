import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)  # the database and cache/ paths are relative to the repository root
sys.path.insert(0, str(ROOT))

import helpers.database as db

# connect() creates the database, or migrates it to the latest schema (after a backup)
with db.connect() as db_conn:
    print(f"{db.db_path}: schema v{db.get_schema_version(db_conn)}")
