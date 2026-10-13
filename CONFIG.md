# Configuration Guide

This boilerplate uses `.env` file to manage deployment settings, allowing you to deploy multiple apps to the same server with different subdirectories.

## Configuration File

The `.env` file in the root directory controls:
- Deployment server and credentials
- Base directory and subdirectory paths
- Production domain
- Flask secret key and other environment variables

### Setup

Copy the example file to create your configuration:
```bash
cp .env.example .env
```

Then edit `.env` with your settings.

### Default Configuration

```env
# Flask Configuration
SECRET_KEY=your-secret-key-here

# Deployment Configuration
DEPLOY_HOST=your.host.com
DEPLOY_USER=your_username
DEPLOY_BASE_DIR=nb/
DEPLOY_SUB_DIR=
DEPLOY_BACKEND_ROOT=/kunden/homepages/29/d557350892/htdocs/
PRODUCTION_DOMAIN=https://yourdomain.com
```

### Configuration Options

#### DEPLOY_HOST
The server hostname for SSH/rsync deployment.

#### DEPLOY_USER
The SSH username for server access.

#### DEPLOY_BASE_DIR
The base directory on your server where apps are deployed (e.g., `nb/`).
- Must end with `/`
- This is the root directory that contains all your apps

#### DEPLOY_SUB_DIR (optional)
A subdirectory within baseDir for this specific app.
- Leave empty to deploy directly to baseDir
- Add a subdirectory (e.g., `myapp/`) to deploy to `baseDir/subDir`
- Must end with `/` if specified

#### PRODUCTION_DOMAIN
The full production domain URL (e.g., `https://yourdomain.com`).

### Password Recovery Email

Set these values in the project-root `.env` (never commit real credentials):

```env
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_SECURE=false
SMTP_FROM_EMAIL=accounts@yourdomain.com
SMTP_FROM_NAME="Your App"
SMTP_USERNAME=accounts@yourdomain.com
SMTP_PASSWORD="your-smtp-password"
```

`SMTP_SECURE=false` uses STARTTLS (typically port 587), not unencrypted SMTP.
`SMTP_SECURE=true` uses implicit TLS (typically port 465). Certificate verification
is enabled in both modes. The SMTP provider must authorize the sender address.

`PASSWORD_RESET_URL` optionally overrides the complete frontend reset-page URL,
including any subdirectory, with no query or fragment. Local development defaults
to `http://localhost:3000/<DEPLOY_SUB_DIR>/reset-password`; deployment defaults to
`<PRODUCTION_DOMAIN>/<DEPLOY_SUB_DIR>/reset-password`, matching whatever scheme
`PRODUCTION_DOMAIN` uses (HTTPS is strongly recommended but not required). If Vite
uses a different port, set a local override. Remove or update that override before
deployment, since an explicit override is also deployed.

Local startup reads these settings from `.env`, with process environment values
taking precedence. Deployment copies only the session key, SMTP settings, reset
URL, and production cookie setting to the private server configuration outside
the web root; SMTP credentials are never added to the frontend build. Redeploy
after changing SMTP settings. Links expire after 15 minutes and may be used once.
The backend enforces matching passwords with 8-128 characters, uppercase,
lowercase, and a number, then invalidates existing sessions after a reset.

Email recovery requires a registered email account. Existing username-only rows
are preserved but are no longer login identities; register new email accounts.

## Deployment Scenarios

### Scenario 1: Single App (Direct to Base)

Deploy directly to `nb/`:

```json
{
  "deployment": {
    "baseDir": "nb/",
    "subDir": ""
  }
}
```

Result:
- Files deployed to: `nb/backend/`, `nb/frontend/`, `nb/index.html`, etc.
- Accessible at: `https://nosebeep.com/`
- API endpoint: `https://nosebeep.com/backend/api.cgi/`

### Scenario 2: Multiple Apps (Subdirectories)

**App 1** (`config.json`):
```json
{
  "deployment": {
    "baseDir": "nb/",
    "subDir": "game1/"
  }
}
```

Result:
- Files deployed to: `nb/game1/backend/`, `nb/game1/frontend/`, etc.
- Accessible at: `https://nosebeep.com/game1/`
- API endpoint: `https://nosebeep.com/game1/backend/api.cgi/`

**App 2** (separate project with different `config.json`):
```json
{
  "deployment": {
    "baseDir": "nb/",
    "subDir": "game2/"
  }
}
```

Result:
- Files deployed to: `nb/game2/backend/`, `nb/game2/frontend/`, etc.
- Accessible at: `https://nosebeep.com/game2/`
- API endpoint: `https://nosebeep.com/game2/backend/api.cgi/`

## How It Works

### Backend Configuration

1. **deploy.sh**: Reads `config.json` and constructs the deployment path
2. **api.cgi**: A shell launcher resolves Apache's script path and invokes the adjacent virtualenv Python and `cgi_app.py`. No deploy-time path substitution or `config.json` is required. Deployment transfers `SECRET_KEY`, SMTP settings, and the reset URL from `.env` into `~/.config/react-python-sqlite-boilerplate/session.json` (permissions 600, outside the web root), setting `FLASK_ENV=production` (and therefore secure-only cookies) only when `PRODUCTION_DOMAIN` uses HTTPS; CGI loads it on every request. Local startup reads the project `.env`, with process environment values taking precedence. Missing keys stop startup; there is no hardcoded fallback. Keep the key stable across deployments to preserve sessions. For multiple deployed apps, configure a separate private path in each app's deployment script and CGI entry point.
3. **config_helper.py**: A local helper that can parse `config.json` when running locally; not required on the server when using deploy-time templating.

### Frontend Configuration

1. **generate-config.js**: Reads `config.json` and generates `frontend/src/config.js`
2. **vite.config.js**: Reads `config.json` to set the base URL for asset paths
3. **api.js**: Imports generated config to construct correct API URLs
4. **npm run build**: Automatically runs `generate-config.js` before building

## Usage

### Initial Setup

1. Copy this boilerplate to a new directory for each app
2. Edit `config.json` with your deployment settings
3. Set `subDir` to a unique value for each app

### Development

No configuration changes needed - development always uses local proxies.

```bash
cd frontend
npm run dev
```

### Building for Production

The build process automatically generates the correct configuration:

```bash
cd frontend
npm run build
```

Or manually generate the config:

```bash
cd frontend
npm run generate-config
```

### Deployment

Simply run the deployment script:

```bash
./deploy.sh
```

The script reads `config.json` and deploys to the configured location.

## Troubleshooting

### Config Not Found

If `config.json` is missing:
- **deploy.sh**: Will exit with an error
- **api.cgi**: Resolves its adjacent virtualenv and Python entry point using Apache's script path.
- **Frontend build**: Uses default base path `/`

### Wrong Paths After Deployment

1. Verify `config.json` has correct `baseDir` and `subDir`
2. Rebuild the frontend: `cd frontend && npm run build`
3. Redeploy: `./deploy.sh`

### API Not Found (404)

Check that:
1. `subDir` in `config.json` matches your deployment
2. Frontend was built after updating `config.json`
3. Backend `api.cgi` and `config_helper.py` were deployed together

## Best Practices

1. **Keep config.json in version control** - But consider using environment-specific configs
2. **Use consistent subdirectory naming** - Use lowercase, hyphens, and trailing slashes
3. **Test locally first** - Always test with `npm run dev` before deploying
4. **Rebuild after config changes** - Always rebuild frontend after changing `config.json`
5. **One config per app** - Each app should have its own `config.json` with unique `subDir`
