# react-python-sqlite-boilerplate

A small React frontend paired with a Python/Flask backend (SQLite) — configured to run locally for development and as a CGI-backed app on shared hosting.

THe backend currently contains a demo "Todo List" app, as is traditional.

Modify .env to deploy to different subdirectories or domains (see .env.example)

## Quick links
- Backend: backend/app.py
- DB schema + sample data: backend/init_db.py
- Deploy script: deploy.sh
- Config: .env (template: .env.example)

## Run locally

Create your project `.env` from `.env.example` and set a random `SECRET_KEY`
before starting the backend. Local Flask startup loads this key automatically;
the development fallback key has been removed.

Backend (development)

```bash
cd backend
# optional: python3 -m venv .venv && source .venv/bin/activate
pip3 install -r requirements.txt
python3 init_db.py    # creates database/app.db and sample items
DEBUG=1 python3 app.py
```

Service listens on http://127.0.0.1:8000 by default.

Frontend (development)

```bash
cd frontend
npm install
npm run dev
```

Dev frontend runs on http://localhost:3000 and proxies API requests to the backend.  `npm run dev` will also start the backend server.

## API (current)

- GET / — root info
- GET /items — list items (returns `{ items: [...] }`)
- POST /items — create item (JSON body with `name`, optional `description`)
- GET /items/<id> — fetch item
- PUT /items/<id> — update item
- DELETE /items/<id> — delete item


See `backend/app.py` for authoritative endpoint behavior.

## Account pages

The header profile icon links to `/auth` for guests and `/profile` for logged-in
users. Login/register tabs share responsive card styling, labeled fields,
keyboard navigation, submission states, and API error messages. The profile
page displays the email address and a styled Log out button. Logout failures retain
the account and show an error; successful logout clears displayed todos and
returns home. Direct profile visits wait for authentication before redirecting
guests. The todo list and its item endpoints remain unchanged.

Login and registration use email addresses. The "Forgot my password" link opens
an email request form; emailed single-use links let users enter and confirm a new
password and expire after 15 minutes. Successful resets revoke existing sessions.
Set SMTP values in `.env`; see [the email configuration guide](CONFIG.md#password-recovery-email).
Legacy username-only accounts are preserved in the database but cannot log in;
create new email-based accounts.

## Database

SQLite DB: `backend/database/app.db`.
To initialize or reset sample data:

```bash
python3 backend/init_db.py
```

## Deployment

The deployment process is managed by the `deploy.sh` script, which uses `.env` for all environment and path settings.

### Setup

Before deploying, create a `.env` file based on `.env.example`:
```bash
cp .env.example .env
# Edit .env with your deployment settings
```

### Standard Deploy

```bash
./deploy.sh
```
- Syncs backend and frontend code to the configured remote directory.
- Copies the session key, SMTP settings, and reset URL to private server configuration
  outside the web root, without printing secrets. No `.env` upload is needed;
  see [backend/README.md](backend/README.md).
- Installs backend Python dependencies in a virtual environment (if not already present).
- Initializes the backend database **only if** this is the first deploy (i.e., the backend directory did not previously exist).
- Does **not** overwrite an existing database or sample data on redeploys.
- Applies additive account-schema migrations on every deploy.

### Force Fresh Deploy (reset database/sample data)

```bash
./deploy.sh --force-fresh
```
- Performs all steps of a standard deploy.
- **Always** (re)initializes the backend database by running `init_db.py` on the server, even if the backend directory already exists.
- This deletes the existing database before initialization. All accounts and
  todos are lost; do not use this flag for routine redeploys.

**Note:**
- The deploy script uses SSH and rsync. You must have SSH access to the configured host.
- The backend virtual environment and dependencies are managed automatically on the server.
- The frontend is built locally and deployed to the correct subdirectory as configured.

See `deploy.sh` for full details and customization options.
