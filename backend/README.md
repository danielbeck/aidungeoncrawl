# Backend README

## Overview

This backend provides a REST API for managing items, using Python Flask and SQLite. It is designed to run both as a Flask server (for local development) and as a CGI application (for shared hosting environments).

## Directory Structure and File Explanations

- **app.py**: Main Flask application. Sets up the app, logging, error handling, database helpers, and registers all endpoints (via Blueprints).
- **api.cgi**: Shell CGI launcher that uses Apache's `SCRIPT_FILENAME` to run the adjacent virtualenv Python and `cgi_app.py`, avoiding unreliable Python shebangs and CGI working directories.
- **cgi_app.py**: Python CGI entry point; resolves its backend directory and logs startup/import failures in `logs/cgi_error.log`.
- **session_secret.py**: Loads private account configuration and the session signing key without a hardcoded fallback.
- **account_schema.py**: Additive, idempotent email-account and password-reset migrations.
- **reset_email.py**: Sends password reset emails using authenticated TLS-protected SMTP.
- **config_helper.py**: Utility for reading deployment configuration from config.json and determining backend paths.
- **init_db.py**: Script to initialize the SQLite database and create sample data.
- **logging_helper.py**: Helper for logging exceptions from CGI scripts to backend/logs/cgi_error.log.
- **requirements.txt**: Python dependencies for the backend (Flask, Flask-CORS, etc).
- **endpoints/**: Directory containing modularized Flask Blueprints for each API endpoint.
    - **items.py**: Blueprint for all /items endpoints (CRUD for items).
    - **auth.py**: Email registration/login, logout, current-user, and password recovery endpoints.
- **database/**: Contains the SQLite database file (app.db).
- **logs/**: Stores log files for Flask and CGI error logging.
    - **flask_error.log**: Logs unhandled exceptions from Flask app.
    - **cgi_error.log**: Logs exceptions from CGI scripts.
    - **server.log**: (Optional) General server logs.
- **__pycache__/**: Python bytecode cache (auto-generated).

## Setup

Set a random, stable `SECRET_KEY` in the project-root `.env`. Local startup
loads that key unless the process environment overrides it. Missing or empty
keys stop startup. Do not commit `.env` or copy it into the web root.

Deployment transfers the key, SMTP settings, reset URL, and production cookie setting over SSH, without printing secrets, to
`~/.config/react-python-sqlite-boilerplate/session.json` outside the web root.
The directory uses permissions 700 and the file 600. CGI reads this private
file on every request; `APP_SECRET_FILE` overrides the path. Changing the key
invalidates existing sessions, not accounts or todos. For additional deployed
apps, use a distinct private path in both `deploy.sh` and `cgi_app.py`.

`FLASK_ENV=production` enables secure-only cookies and is only set automatically
during deployment when `PRODUCTION_DOMAIN` uses HTTPS; plain-HTTP deployments
keep non-secure cookies so sessions still work. A secret key does not encrypt
HTTP traffic.

Authentication uses Flask-Limiter's automatic request hook, not a manual
`check()` call on its extension, which newer releases store as a set.

Deployment checks that the virtualenv's recorded Python major/minor version
matches its interpreter and that pip is importable. Hosting Python upgrades can
invalidate an old virtualenv. In that case the old environment is preserved
under `backend/venv-backup.*/venv` and a fresh one is created. All dependencies
are installed using `venv/bin/python -m pip`; installation or import failures
stop deployment instead of falling back to an incomplete dependency list.

1. Install Python dependencies:
    ```bash
    pip3 install -r requirements.txt
    ```
2. Initialize the database:
    ```bash
    python3 init_db.py
    ```
    This will create:
    - `database/app.db` - SQLite database file
    - 3 sample items

3. Start the Flask development server:
    ```bash
    python3 app.py
    ```

## API Examples

- `POST /auth/register` and `POST /auth/login`: `{"email":"person@example.com","password":"YourPassword123"}`
- `POST /auth/logout`: clear the login session
- `GET /auth/me`: current email or `null`
- `POST /auth/forgot-password`: `{"email":"person@example.com"}`; returns the same generic confirmation for known and unknown emails
- `POST /auth/reset-password`: `{"token":"<emailed-token>","password":"NewPassword123","confirm_password":"NewPassword123"}`

Password reset links expire exactly 900 seconds after issuance, are single-use,
and are stored only as SHA-256 hashes. A replacement link invalidates the previous
link; requests for the same account within 60 seconds do not resend. This cooldown
is stored in SQLite and works across CGI processes. SMTP errors are logged and
return HTTP 503 rather than claiming delivery succeeded. Password validation and
confirmation failures do not consume the link. Successful resets invalidate all
existing sessions for that account and require a fresh login.

Links use `/reset-password#token=...` so the token is not sent in HTTP URLs or
access logs; the UI removes the fragment after capturing it. Opening the email
link does not consume it; only submitting the new password does.

Existing databases are migrated at startup and deployment without deleting users
or todos. Legacy username-only accounts have no email and cannot log in or request
recovery; register new email-based accounts. The legacy database `username` column
remains for schema compatibility and stores the email for new accounts.

Configure SMTP and link destinations as described in [CONFIG.md](../CONFIG.md).
To run the account regression suite from the repository root:

```bash
python3 -m unittest backend.test_account_backport backend.test_password_reset
```

The frontend uses the Vite `<BASE_URL>api` proxy in development and the CGI URL
in production. Backend JSON validation errors are shown on the account forms;
non-JSON responses explicitly report a backend/routing failure.

### Get All Items
```bash
curl http://localhost:8000/items
```

### Create New Item
```bash
curl -X POST http://localhost:8000/items \
  -H "Content-Type: application/json" \
  -d '{"name":"New Item","description":"Optional description"}'
```

### Update Item
```bash
curl -X PUT http://localhost:8000/items/1 \
  -H "Content-Type: application/json" \
  -d '{"name":"Updated Item","description":"New description"}'
```

### Delete Item
```bash
curl -X DELETE http://localhost:8000/items/1
```

## CORS

CORS is enabled using Flask-CORS to allow frontend access from any origin.
