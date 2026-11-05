# Backend

Flask and SQLite provide an authenticated text-adventure API. The application
owns canonical state; LLMs interpret actions and narrate resolved outcomes.
It runs as a local Flask server or as CGI on shared hosting.

## Layout

| Path | Responsibility |
|---|---|
| `app.py` | Flask setup, logging, database helpers, response controls, and blueprints. |
| `api.cgi`, `cgi_app.py` | Apache CGI launcher and Python entry point. |
| `session_secret.py` | Local/private settings and required session key. |
| `account_schema.py`, `init_db.py` | Additive account migrations and initial database setup. |
| `reset_email.py` | TLS-protected password-reset email. |
| `game_engine.py` | State, entity identity, and turn orchestration. |
| `tool_mode.py` | Native game-master conversation and transactional world tools. |
| `endpoints/` | Account, gameplay, and LLM APIs. |
| `harness/` | Provider adapters and reusable LLM call mechanics. |
| `llm_calls/`, `../shared/llm/` | Game-specific LLM tasks and prompt/schema definitions. |
| `config_helper.py` | Legacy environment/config.json deployment helper. |
| `database/`, `logs/` | Private SQLite data and diagnostic output. |

## Setup and hosting

Follow the root [development setup](../README.md#run-locally). A stable, non-empty
`SECRET_KEY` is required in every environment. Configuration sources, SMTP,
provider settings, and production paths are documented in [CONFIG.md](../CONFIG.md).

The CGI shell launcher uses Apache's `SCRIPT_FILENAME` to locate its adjacent
virtualenv and Python entry point. Startup/import failures go to
`logs/cgi_error.log`. Private settings are read on each CGI request; changing the
session key invalidates sessions, not accounts or saved adventures.

Deployment checks the virtualenv's Python major/minor version and pip. If hosting
upgrades invalidate it, the old environment is preserved under
`venv-backup.*/venv` and a fresh one is created. Dependencies are installed through
`venv/bin/python -m pip`; installation/import failures stop deployment.

## Account API

| Endpoint | Request or result |
|---|---|
| `POST /auth/register` | `{"email":"person@example.com","password":"YourPassword123"}` |
| `POST /auth/login` | Same request fields as registration. |
| `POST /auth/logout` | Clear the login session. |
| `GET /auth/me` | Current email or `null`. |
| `POST /auth/forgot-password` | `{"email":"person@example.com"}` |
| `POST /auth/reset-password` | `{"token":"<emailed-token>","password":"NewPassword123","confirm_password":"NewPassword123"}` |

Emails are normalized for case-insensitive lookup and uniqueness. Passwords
require 8-128 characters including uppercase, lowercase, and a number.

Recovery requests return the same generic confirmation for known and unknown
accounts. Links expire after 900 seconds and are stored only as SHA-256 hashes.
A replacement invalidates the old link; a persistent 60-second per-account
cooldown prevents resending across CGI processes. SMTP failures are logged and
return HTTP 503 rather than claiming delivery succeeded.

Links use `/reset-password#token=...` to keep the token out of HTTP URLs and
access logs. The UI removes the fragment after capture. Opening the link does
not consume it; only a successful reset does. Password validation failures
preserve the token. Successful resets revoke existing sessions and require login.

Account migrations preserve existing rows. Legacy username-only accounts cannot
log in or request recovery; register an email-based account. The legacy username
column remains for compatibility and stores the email for new accounts.

The frontend uses Vite's development proxy or the production CGI path. Account
forms display JSON errors and explicitly report non-JSON routing/backend failures.

## Gameplay API

All gameplay endpoints require an authenticated session.

| Endpoint | Purpose |
|---|---|
| `GET /game/` | Load or initialize the user's saved adventure. |
| `POST /game/new` | Replace it with a fresh adventure; optional JSON `starting_point`: `town` (default), `wilderness`, or `dungeon`. |
| `POST /game/turn` | Process `action`, `game_id`, and UUID `request_id`. |
| `POST /game/mode` | Persist `mode` for `game_id`: `managed`, `direct`, `freeform`, or `tool_using`. |
| `POST /game/prepare` | Prepare summarized history for `game_id`. |
| `POST /game/stop` | End the adventure. |

The starting point sets both the initial location and opening narration for all
storytelling modes. An omitted body or field preserves the town-square default;
invalid starting points return HTTP 400 without replacing the saved adventure.

```bash
curl -b cookies.txt -c cookies.txt http://localhost:8000/game/
curl -b cookies.txt -c cookies.txt -X POST http://localhost:8000/game/turn \
  -H "Content-Type: application/json" \
  -d '{"action":"Enter the tavern","game_id":"<game-id>","request_id":"<uuid>"}'
```

In Managed mode, managers report inventory/NPC interactions and movement intent. Code reconciles
these before the narrator returns only `narration`, `action_succeeded`, and
`game_over`. There is no narrator state-operation payload or correction loop.
Supported persistent changes and component contracts are documented once in
[Implementation](../docs/IMPLEMENTATION.md).

`POST /game/turn` also accepts `mode` (default `managed`). Tool using mode requires
a model supporting native function tools. Its game master updates the draft world
through nine map/item/NPC tools, receives actual results, and returns story text.
Unsupported providers/models fail explicitly without saving draft changes.

Saving is atomic and version-checked. Reusing a completed `request_id` returns
the saved result. Invalid model responses do not partially change state.
Turn errors include the LLM or validation failure reason in both JSON and streamed
responses, so the gameplay error and debug progress explain why the turn failed.
Rejected manager output appears in the failed manager's debug Response section.
Provider transport-error details and tracebacks are not included.
Managed and Tool using summary preparation runs in a separate post-turn request
triggered by the frontend after displaying saved narration. Summary failures are
logged without discarding the completed turn; late results are revision-checked.

### Streaming

`POST /game/turn` accepts `Accept: application/x-ndjson`. Each line has type
`progress`, `heartbeat`, `complete`, or `error`. Progress carries component/status
and, where available, request, response, provider, and model details. Terminal
events contain `status` and `data`. Failures before streaming use HTTP JSON
errors. Non-streaming JSON clients remain supported.

Disable proxy buffering for immediate progress delivery; responses set
`X-Accel-Buffering: no`.

### Diagnostics

The turn-progress panel shows intermediate component reports and saving status;
these are debug information, not committed game state. Only the final saved
response updates story and world displays.

Structured calls log starts, raw responses, provider/model, call IDs, elapsed
time, and validation/provider failures. Parallel calls are correlated by ID.
Diagnostics go to stderr and `logs/llm.log`, rotating at 2 MiB with three backups.
Raw response previews are limited to 32,768 characters and marked when truncated;
game-context previews are limited to 4,000 characters.

Logs contain player input, context, and model output: treat them as private.
Provider settings and authentication headers are not logged.

Tool using mode logs complete game-master messages and ordered tool starts,
results, and errors to the same diagnostic log, including nested comparator
requests. Its progress events carry `operation_id` and, for nested calls,
`parent_operation_id`, so repeated tools retain independent ordered entries.

## Testing

Run focused suites from the repository root as needed:

```bash
python3 -m unittest backend.test_account_backport backend.test_password_reset
python3 -m unittest backend.test_game_engine backend.test_game_api backend.test_history_preparation
python3 -m unittest backend.test_tool_mode backend.test_tool_provider
```

Live movement checks are opt-in and call local Ollama:

```bash
RUN_LIVE_MOVEMENT_TESTS=1 python3 -m unittest backend.test_movement_live
```

For cookie, CORS, CSRF, and rate-limit boundaries, see
[Security Implementation](../SECURITY_IMPLEMENTATION.md).
