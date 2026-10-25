import helpers.database as db

# connect() creates the database, or migrates it to the latest schema (after a backup)
with db.connect() as db_conn:
    print(f"{db.db_path}: schema v{db.get_schema_version(db_conn)}")
