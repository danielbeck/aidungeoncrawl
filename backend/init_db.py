import sqlite3
import os
try:
    from backend.account_schema import ensure_account_schema
except ImportError:
    from account_schema import ensure_account_schema

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, 'database')
DB_PATH = os.path.join(DB_DIR, 'app.db')

def init_database():
    """Initialize the database with tables and sample data"""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    ensure_account_schema(conn)

    conn.commit()
    conn.close()
    print(f"\nDatabase initialized at {DB_PATH}")

if __name__ == '__main__':
    init_database()