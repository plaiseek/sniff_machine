import helpers.database as db

with db.connect() as db_conn:
    db.create_tables(db_conn)

