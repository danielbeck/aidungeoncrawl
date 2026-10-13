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

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')

    ensure_account_schema(conn)

    # Add sample items for the first user if exists
    cursor.execute('SELECT id FROM users ORDER BY id LIMIT 1')
    row = cursor.fetchone()
    if row:
        user_id = row[0]
        cursor.execute('SELECT COUNT(*) FROM items WHERE user_id = ?', (user_id,))
        if cursor.fetchone()[0] == 0:
            sample_items = [
                (user_id, 'Sample Item 1', 'This is a sample description'),
                (user_id, 'Sample Item 2', 'Another sample item'),
                (user_id, 'Sample Item 3', 'Yet another one'),
            ]
            cursor.executemany('INSERT INTO items (user_id, name, description) VALUES (?, ?, ?)', sample_items)
            print(f"Created {len(sample_items)} sample items for user_id {user_id}")

    conn.commit()
    conn.close()
    print(f"\nDatabase initialized at {DB_PATH}")

if __name__ == '__main__':
    init_database()