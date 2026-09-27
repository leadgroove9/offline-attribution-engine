import os
import sqlite3
import psycopg2
from config import DATABASE_URL

DB_PATH = "offline_attribution.db"

class PostgreSQLCursorWrapper:
    def __init__(self, pg_cursor):
        self._cursor = pg_cursor

    def execute(self, query, params=None):
        pg_query = query.replace("?", "%s")
        pg_query = pg_query.replace("AUTOINCREMENT", "SERIAL")
        if params is None:
            return self._cursor.execute(pg_query)
        return self._cursor.execute(pg_query, params)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

class PostgreSQLConnectionWrapper:
    def __init__(self, pg_conn):
        self._conn = pg_conn

    def cursor(self):
        return PostgreSQLCursorWrapper(self._conn.cursor())

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

class DatabaseRouter:
    def connect(self):
        if DATABASE_URL:
            try:
                pg_conn = psycopg2.connect(DATABASE_URL)
                return PostgreSQLConnectionWrapper(pg_conn)
            except Exception as e:
                print(f"⚠️ PostgreSQL connection failed ({e}). Falling back to local SQLite ({DB_PATH}).")
        
        db_file = DB_PATH
        if not os.path.exists(db_file) and os.path.exists(os.path.join("/workspace", DB_PATH)):
            db_file = os.path.join("/workspace", DB_PATH)
        return sqlite3.connect(db_file)

db_router = DatabaseRouter()
