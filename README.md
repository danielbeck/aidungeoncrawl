# AI Dungeon Crawl

A text-based fantasy adventure built with React, Flask, and SQLite. The Dungeon
Master narrates free-text player actions after the backend reconciles manager
interpretations against canonical game state.

## Quick links
- [Product scope](docs/PRODUCT_SPEC.md)
- [Turn architecture](docs/IMPLEMENTATION.md)
- [Backend API and tests](backend/README.md)
- [Configuration](CONFIG.md) (template: [.env.example](.env.example))
- [Security controls and limitations](SECURITY_IMPLEMENTATION.md)

## Run locally

Create your project `.env` from `.env.example` and set a random `SECRET_KEY`
before starting the backend. Local Flask startup loads this key automatically;
there is no fallback key.

Install dependencies and initialize account storage:

```bash
pip3 install -r backend/requirements.txt
python3 backend/init_db.py
npm --prefix frontend install
```

Then start both servers from the repository root:

```bash
./dev.sh
```

The backend listens on http://127.0.0.1:8000 and the frontend on
http://localhost:3000 (under `DEPLOY_SUB_DIR` when configured), proxying API calls.
The script stops existing listeners on ports 8000 and 3000 before startup, so
reserve those ports for this project. It refuses to start if a port remains
occupied and stops its backend process on exit.

Alternatively, `cd frontend && npm run dev` starts both through npm. For separate
terminals, run `DEBUG=1 python3 backend/app.py` and
`cd frontend && NODE_ENV=development npm run generate-config && npm run vite`.
Do not run multiple startup methods at once.

## Gameplay API

- `GET /game/` — load or initialize the authenticated user's adventure
- `POST /game/new` — start a new adventure
- `POST /game/turn` — process a free-text action (`action`, `game_id`, `request_id`)
- `POST /game/prepare` — prepare summarized history for the next turn
- `POST /game/stop` — end the current adventure

See the [backend guide](backend/README.md#gameplay-api) for contracts and streaming.

## Account pages

The header profile icon links to `/auth` for guests and `/profile` for logged-in
users. Login/register tabs share responsive card styling, labeled fields,
keyboard navigation, submission states, and API error messages. The profile
page displays the email address and a styled Log out button. Logout failures retain
the account and show an error; successful logout returns home. Direct profile
visits wait for authentication before redirecting guests. The adventure remains
saved when the player logs out.

Login and registration use email addresses. The "Forgot my password" link opens
an email request form; emailed single-use links let users enter and confirm a new
password and expire after 15 minutes. Successful resets revoke existing sessions.
Set SMTP values in `.env`; see [the email configuration guide](CONFIG.md#password-recovery-email).
Legacy username-only accounts are preserved in the database but cannot log in;
create new email-based accounts.

The default SQLite database is `backend/database/app.db`. Account-schema
migrations are additive; initialization is part of the first-time setup above.

## Deployment

The deployment process is managed by the `deploy.sh` script, which uses `.env` for all environment and path settings.

Set the deployment values in your project `.env` as described in
[Configuration](CONFIG.md#deployment-and-paths).

### Standard Deploy

```bash
./deploy.sh
```
- Syncs backend and frontend code to the configured remote directory.
- Copies the session key, SMTP and LLM settings, and reset URL to private server
  configuration outside the web root, without printing secrets. No `.env`
  upload is needed; see [private settings](CONFIG.md#private-server-settings).
- Installs backend Python dependencies in a virtual environment (if not already present).
- Initializes the backend database **only if** this is the first deploy (i.e., the backend directory did not previously exist).
- Does **not** overwrite an existing database on redeploys.
- Applies additive account-schema migrations on every deploy.

### Force Fresh Deploy (reset database)

```bash
./deploy.sh --force-fresh
```
- Performs all steps of a standard deploy.
- **Always** (re)initializes the backend database by running `init_db.py` on the server, even if the backend directory already exists.
- This deletes the existing database before initialization. All accounts and
  adventures are lost; do not use this flag for routine redeploys.

**Note:**
- The deploy script uses SSH and rsync. You must have SSH access to the configured host.
- The backend virtual environment and dependencies are managed automatically on the server.
- The frontend is built locally and deployed to the correct subdirectory as configured.

See [deploy.sh](deploy.sh) for implementation details and
[CONFIG.md](CONFIG.md) for configuration and troubleshooting.
