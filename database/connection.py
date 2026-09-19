import os
import re
import sqlite3

class PostgreSQLCursorWrapper:
    def __init__(self, pg_cursor):
        self.cursor = pg_cursor

    def execute(self, query, params=None):
        pg_query = query.replace("?", "%s")
        pg_query = re.sub(r'INTEGER PRIMARY KEY AUTOINCREMENT', 'SERIAL PRIMARY KEY', pg_query, flags=re.IGNORECASE)
        pg_query = re.sub(r'TIMESTAMP DEFAULT CURRENT_TIMESTAMP', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP', pg_query, flags=re.IGNORECASE)
        
        if params is None:
            self.cursor.execute(pg_query)
        else:
            self.cursor.execute(pg_query, params)
        return self

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

class PostgreSQLConnectionWrapper:
    def __init__(self, pg_conn):
        self.conn = pg_conn

    def cursor(self):
        return PostgreSQLCursorWrapper(self.conn.cursor())

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

class DatabaseRouter:
    def __init__(self, db_path="offline_attribution.db"):
        self.db_path = db_path
        self.db_url = os.environ.get("DATABASE_URL")

    def connect(self):
        if self.db_url:
            try:
                import psycopg2
                pg_conn = psycopg2.connect(self.db_url)
                return PostgreSQLConnectionWrapper(pg_conn)
            except Exception as e:
                print(f"⚠️ PostgreSQL connection failed ({e}), falling back to SQLite ({self.db_path}).")
        
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

db_router = DatabaseRouter()
