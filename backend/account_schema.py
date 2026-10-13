def ensure_account_schema(db):
    """Add email accounts and reset tokens without discarding existing users or items."""
    columns = {row[1] for row in db.execute('PRAGMA table_info(users)')}
    objects = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE name IN ('users_email', 'password_resets')"
    )}
    if {'email', 'auth_version'} <= columns and objects == {'users_email', 'password_resets'}:
        return
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    columns = {row[1] for row in db.execute('PRAGMA table_info(users)')}
    if 'email' not in columns:
        db.execute('ALTER TABLE users ADD COLUMN email TEXT COLLATE NOCASE')
    if 'auth_version' not in columns:
        db.execute('ALTER TABLE users ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS users_email ON users(email COLLATE NOCASE)')
    db.execute('''
        CREATE TABLE IF NOT EXISTS password_resets (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL UNIQUE,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')
    db.commit()
