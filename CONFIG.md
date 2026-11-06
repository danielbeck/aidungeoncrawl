# Configuration Guide

The project-root `.env` is the configuration source for local development,
frontend path generation, and deployment. Do not commit it.

## Setup

```bash
cp .env.example .env
```

Replace the example values, especially `SECRET_KEY`, with your own settings.
The backend requires a non-empty key in every environment; there is no
development fallback. Keep the key stable to preserve sessions.

## Deployment and paths

| Variable | Purpose |
|---|---|
| `DEPLOY_HOST` | SSH/rsync server hostname. |
| `DEPLOY_USER` | SSH username. |
| `DEPLOY_BASE_DIR` | Remote deployment base directory, such as `apps/`. |
| `DEPLOY_SUB_DIR` | Optional app subdirectory, such as `game1/`; empty deploys directly to the base directory. |
| `DEPLOY_BACKEND_ROOT` | Absolute server document-root path used to construct backend paths. |
| `PRODUCTION_DOMAIN` | Public origin, such as `https://example.com`. |

For separate apps, use separate project configurations and subdirectories:

```env
DEPLOY_BASE_DIR=apps/
DEPLOY_SUB_DIR=game1/
PRODUCTION_DOMAIN=https://example.com
```

This deploys under `apps/game1/`, with the frontend at
`https://example.com/game1/` and API at
`https://example.com/game1/backend/api.cgi`.
Use lowercase, hyphenated subdirectory names and trailing slashes for directory
settings. Set `DEPLOY_SUB_DIR=` for a root deployment.

### How paths are generated

- `deploy.sh` reads `.env` and constructs remote paths.
- `frontend/generate-config.js` generates `frontend/src/config.js` and Apache
  rewrite configuration. The generated frontend config contains public paths,
  not SMTP or provider credentials.
- `frontend/vite.config.js` reads `DEPLOY_SUB_DIR` from `.env` for asset paths
  and the development proxy.
- Development uses `/<subdirectory>/api` proxied to Flask on port 8000;
  production uses `/<subdirectory>/backend/api.cgi`.
- `api.cgi` locates its adjacent virtualenv and `cgi_app.py` using Apache's
  script path, without deploy-time path substitution.

`npm run dev` generates development paths. `npm run build` generates production
paths before building. After changing path settings, restart the development
server or rebuild and redeploy for production.

### Private server settings

Deployment transfers `SECRET_KEY`, SMTP settings, LLM settings, the reset URL,
and the cookie-environment setting over SSH to
`~/.config/react-python-sqlite-boilerplate/session.json`, outside the web root.
Its directory has permissions 700 and the file 600; `.env` is not uploaded.
CGI reads this file on each request through `APP_SECRET_FILE`. For multiple apps,
configure distinct private paths in each app's deployment script and CGI entry
point.

Local backend settings load from `.env`, with process environment values taking
precedence. When `APP_SECRET_FILE` is set, the private JSON file is used instead.
Deployment sets `FLASK_ENV=production` for HTTPS domains, enabling secure-only
cookies; HTTP domains use `development` so cookies still work. HTTPS is strongly
recommended: a session key does not encrypt traffic.

## Password recovery email

```env
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_SECURE=false
SMTP_FROM_EMAIL=accounts@example.com
SMTP_FROM_NAME="AI Dungeon Crawl"
SMTP_USERNAME=accounts@example.com
SMTP_PASSWORD="your-smtp-password"
```

`SMTP_SECURE=false` uses verified STARTTLS, typically on port 587.
`SMTP_SECURE=true` uses verified implicit TLS, typically on port 465. The
provider must authorize the sender address.

`PASSWORD_RESET_URL` optionally overrides the complete frontend reset-page URL,
including any subdirectory, without a query or fragment:
- Local default: `http://localhost:3000/<DEPLOY_SUB_DIR>/reset-password`.
- Deployment default: `<PRODUCTION_DOMAIN>/<DEPLOY_SUB_DIR>/reset-password`.

Set a local override if Vite uses another port. Remove or update it before
deployment, since explicit overrides are also deployed. Redeploy after changing
SMTP settings. For link expiry, password rules, and account behavior, see the
[account API guide](backend/README.md#account-api).

## LLM settings

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` | Local provider: `ollama` or `openrouter`; defaults to Ollama. Not deployed. |
| `PRODUCTION_LLM_PROVIDER` | Provider written by `deploy.sh`; defaults to `openrouter`. |
| `OLLAMA_URL`, `OLLAMA_MODEL` | Local provider endpoint and model. |
| `OPENROUTER_URL`, `OPENROUTER_MODEL` | OpenRouter endpoint and model. |
| `OPENROUTER_API_KEY` | Private OpenRouter credential. |

OpenRouter requests send `HTTP-Referer` using `PRODUCTION_DOMAIN` plus
`DEPLOY_SUB_DIR` (for example, `https://example.com/game1/`). Deployment includes
both settings in the private backend configuration. Without `PRODUCTION_DOMAIN`,
the header defaults to `http://localhost:3000`.

See `.env.example` for defaults. Prompts and response schemas live in
`shared/llm/`; see [shared definitions](shared/README.md).

## Other runtime settings

- `USER_DAILY_TURN_QUOTA` is the per-account daily gameplay turn allowance,
  default 200. Counters live in SQLite, so the limit holds across CGI processes
  and restarts, and reset at UTC midnight. Each accepted `/game/turn` request
  counts once, including retried attempts after a provider failure; replaying a
  completed `request_id` does not count again. Deployment transfers the value
  into the private server configuration.
- `DEBUG=1` enables Flask's local debug mode.
- `DB_PATH` optionally overrides the backend SQLite path.
- `ALLOWED_ORIGINS` is a comma-separated list read from the backend process
  environment; without it, Flask allows localhost ports 3000-3005.
  Unlike the settings transferred above, deployment does not copy this variable
  into the private JSON file, and the CORS initialization does not read it from
  `.env`. Export it in the backend environment if needed. CORS is not an
  authentication or CSRF mechanism.
- `POST /llm/chat` is served only outside production. Deployment writes
  `FLASK_ENV=production` into the private settings, which removes that free-form
  prompt endpoint (any prompt, client-supplied schema) from the deployed API.
  The typed `POST /llm/calls/<name>` routes remain available only to authenticated
  registered users. Development `/llm/chat` requests also require authentication.
  These direct calls do not consume the gameplay turn quota.

## Troubleshooting

- **Missing `.env`:** deployment stops; frontend tools warn and use default paths,
  while backend startup still requires a session key from its supported sources.
- **Wrong assets or API 404s:** check `DEPLOY_SUB_DIR`, rebuild and redeploy, and
  verify that `backend/api.cgi`, its virtualenv, and Apache rewrite rules are
  present at the generated paths.
- **Sessions fail in production:** verify the private configuration is readable,
  the key is stable, and secure-cookie settings match the site's HTTP/HTTPS scheme.
- **Reset links point to the wrong host:** check for a stale
  `PASSWORD_RESET_URL` override and redeploy.

## Legacy configuration helper

`backend/config_helper.py` can still read deployment environment variables or
fall back to `config.json`. This is a legacy helper, not the configuration source
for the current deployment script or frontend tools. New setups should use
`.env`, not create or commit `config.json`.
