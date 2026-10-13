# Backend README

## Overview

This backend provides an authenticated text-adventure API using Python Flask and SQLite. The engine stores authoritative world state and validates structured LLM proposals. It runs as a Flask server locally and as a CGI application on shared hosting.

## Directory Structure and File Explanations

- **app.py**: Main Flask application. Sets up the app, logging, error handling, database helpers, and registers all endpoints (via Blueprints).
- **api.cgi**: Shell CGI launcher that uses Apache's `SCRIPT_FILENAME` to run the adjacent virtualenv Python and `cgi_app.py`, avoiding unreliable Python shebangs and CGI working directories.
- **cgi_app.py**: Python CGI entry point; resolves its backend directory and logs startup/import failures in `logs/cgi_error.log`.
- **session_secret.py**: Loads private account configuration and the session signing key without a hardcoded fallback.
- **account_schema.py**: Additive, idempotent email-account and password-reset migrations.
- **reset_email.py**: Sends password reset emails using authenticated TLS-protected SMTP.
- **config_helper.py**: Utility for reading deployment configuration from config.json and determining backend paths.
- **init_db.py**: Script to initialize account storage; gameplay tables are created when needed.
- **game_engine.py**: Authoritative game-state model, proposal validation, and LLM turn orchestration.
- **logging_helper.py**: Helper for logging exceptions from CGI scripts to backend/logs/cgi_error.log.
- **requirements.txt**: Python dependencies for the backend (Flask, Flask-CORS, etc).
- **endpoints/**: Directory containing modularized Flask Blueprints for each API endpoint.
    - **game.py**: Authenticated endpoints for loading, starting, playing, and ending an adventure.
    - **auth.py**: Email registration/login, logout, current-user, and password recovery endpoints.
- **llm_calls/gameplay.py** and **shared/llm/**: Structured calls and prompt/schema definitions for the inventory, map, NPC, narrator, and summarizer roles.
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
invalidates existing sessions, not accounts or saved adventures. For additional deployed
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
or other account data. Legacy username-only accounts have no email and cannot log in or request
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

### Gameplay API

Gameplay endpoints require an authenticated session.

```bash
curl -b cookies.txt -c cookies.txt http://localhost:8000/game/
curl -b cookies.txt -c cookies.txt -X POST http://localhost:8000/game/turn \
  -H "Content-Type: application/json" \
  -d '{"action":"Enter the tavern","game_id":"<game-id>","request_id":"<uuid>"}'
```

`GET /game/` loads or creates the player's saved adventure. `POST /game/new`
replaces it with a fresh adventure; `POST /game/stop` ends it. A turn uses
structured inventory, map, and NPC assessments followed by narration. The server
validates all proposed state changes and commits the turn atomically; `request_id`
makes retries of a completed turn idempotent.

Narrator operations use actual JSON objects for `payload`, not JSON-encoded
strings. Legacy valid string payloads remain readable by the engine. A rejected
narrator response gets one correction attempt with validation feedback against
the unchanged state. If correction also fails, the turn returns an error without
saving any proposed changes; provider connectivity errors are not retried here.
Corrections must retain applicable changes, not erase them to avoid validation.
A correction that drops every operation while repeating the same successful
narration is rejected without saving that turn.

The narrator schema constrains required and allowed payload fields per operation.
Newly acquired portable items use `create_item` with `holder_type: "player"` to
enter inventory directly; `transfer_item` requires an existing item and a target
holder. Ordering or receiving an item does not consume it.
Animals and mounts are tracked as NPCs. The prompts allow plausible first-use
establishment of a mount without treating its absence from inventory as proof
it is missing. Dismounting changes mounted state, not the player's map location.

Repeated `add_exit` proposals are harmless no-ops when the origin already has an
exit to that destination. Unresolved exits match by case-insensitive description
within the origin. Existing IDs, descriptions, and traversability are preserved;
changing traversability requires `set_exit_traversable`.

### LLM diagnostics

The game interface always shows a live turn-progress panel with manager assessments
as each finishes, narrator responses/correction errors, summarization, and saving
status. Intermediate responses may reveal world details and are not committed
state. Only the final saved response updates the story and inventory.

`POST /game/turn` accepts `Accept: application/x-ndjson` for streaming. Each
newline-delimited JSON event has type `progress`, `heartbeat`, `complete`, or
`error`. Progress includes component/status and, on completion, response/provider/model.
Terminal events contain `status` and `data` (the normal JSON result or error).
Validation/authentication failures before streaming still use normal HTTP JSON
errors. Existing JSON clients remain supported. Disable response buffering in
deployment proxies to receive events immediately; the response sets
`X-Accel-Buffering: no`. Retrying the same request ID returns the saved result.

Each structured LLM call logs its start and raw response before parsing or
validation, with a call ID, component name, provider/model, and elapsed time.
Provider and validation failures log the exception without changing game state.
Rejected gameplay operations include their index and payload in the diagnostic
log. Parallel calls can be matched by their call IDs.

Diagnostics appear on stderr (the development server console or CGI error
output) and in `backend/logs/llm.log`. The file rotates at 2 MiB with three
backups. Raw-response previews are limited to 32,768 characters and explicitly
marked when truncated; context previews are limited to 4,000 characters.
These logs contain player input, narrative context, and model output, so treat
them as private. Provider settings and authentication headers are not logged.

## CORS

CORS is enabled using Flask-CORS to allow frontend access from any origin.
