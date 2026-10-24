#!/bin/bash

# Deployment script - Deploys to configured directory
# Configuration is read from .env file
set -o pipefail

# Read configuration from .env file
if [ ! -f ".env" ]; then
    echo "❌ .env not found!"
    echo "Please create .env with deployment settings (see .env.example)"
    exit 1
fi

# Source .env file and export variables
set -a
source .env
set +a

if [ -z "${SECRET_KEY:-}" ]; then
    echo "SECRET_KEY must be set in .env before deploying."
    exit 1
fi

# Normalize paths using Python
eval $(python3 - <<PY
import os
def normslash(s):
    if s and not s.endswith('/'):
        s = s + '/'
    if s == '/':
        s = ''
    return s

base = normslash(os.environ.get('DEPLOY_BASE_DIR', ''))
sub = os.environ.get('DEPLOY_SUB_DIR', '').strip('/')
if sub:
    sub = sub + '/'
else:
    sub = ''
backend_root = normslash(os.environ.get('DEPLOY_BACKEND_ROOT', ''))

print(f"HOST='{os.environ.get('DEPLOY_HOST','')}'")
print(f"USER='{os.environ.get('DEPLOY_USER','')}'")
print(f"BASE_DIR='{base}'")
print(f"SUB_DIR='{sub}'")
print(f"BACKEND_ROOT='{backend_root}'")
print(f"DOMAIN='{os.environ.get('PRODUCTION_DOMAIN','')}'")
PY
)

# Construct full remote directory path (never use DOMAIN for remote paths)
REMOTE_DIR="${BASE_DIR}${SUB_DIR}"
if [ -z "$SUB_DIR" ]; then
    REMOTE_DIR="$BASE_DIR"
fi
echo "[DEBUG] REMOTE_DIR: $REMOTE_DIR"

CONTROL_PATH="$HOME/.ssh/deploy-control-$$"

echo "🚀 Deploying to $USER@$HOST:$REMOTE_DIR"
echo "⚠️  This script will only modify the configured directory"
echo ""

# Establish persistent SSH connection
echo "📡 Establishing SSH connection..."

ssh -o ControlMaster=auto \
    -o ControlPath="$CONTROL_PATH" \
    -o ControlPersist=10m \
    -fN $USER@$HOST

if [ $? -ne 0 ]; then
    echo "❌ Failed to establish SSH connection"
    exit 1
fi

sleep 1
echo "✅ Connection established"
echo ""

# Cleanup function to close SSH connection
cleanup() {
    echo ""
    echo "🔒 Closing SSH connection..."
    ssh -o ControlPath="$CONTROL_PATH" -O exit $USER@$HOST 2>/dev/null
    rm -f "$CONTROL_PATH"
}

trap cleanup EXIT

echo "Installing private account configuration (secrets are not printed)..."
if ! ACCOUNT_CONFIG=$(node -e '
    const keys = ["SECRET_KEY", "SMTP_HOST", "SMTP_PORT", "SMTP_SECURE", "SMTP_FROM_EMAIL", "SMTP_FROM_NAME", "SMTP_USERNAME", "SMTP_PASSWORD", "LLM_PROVIDER", "OLLAMA_URL", "OLLAMA_MODEL", "OPENROUTER_URL", "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "PRODUCTION_DOMAIN", "DEPLOY_SUB_DIR"];
    const settings = Object.fromEntries(keys.map(key => [key, process.env[key]]));
    // LLM_PROVIDER usually targets local Ollama; production needs its own provider.
    settings.LLM_PROVIDER = process.env.PRODUCTION_LLM_PROVIDER || "openrouter";
    if (settings.LLM_PROVIDER === "openrouter" && !settings.OPENROUTER_API_KEY) throw new Error("OPENROUTER_API_KEY is required for the production OpenRouter provider.");
    const domain = process.env.PRODUCTION_DOMAIN;
    if (!domain) throw new Error("PRODUCTION_DOMAIN is required.");
    const sub = (process.env.DEPLOY_SUB_DIR || "").replace(/^\/+|\/+$/g, "");
    settings.PASSWORD_RESET_URL = process.env.PASSWORD_RESET_URL || `${domain.replace(/\/+$/, "")}/${sub ? sub + "/" : ""}reset-password`;
    const resetURL = new URL(settings.PASSWORD_RESET_URL);
    if (resetURL.hash || resetURL.search) throw new Error("Production PASSWORD_RESET_URL must not include a query or fragment.");
    // Secure-only cookies require HTTPS; only mark the deployment "production"
    // (and therefore Secure-only) when the production domain actually uses HTTPS.
    settings.FLASK_ENV = domain.startsWith("https://") ? "production" : "development";
    process.stdout.write(JSON.stringify(settings));
'); then
    echo "Invalid private account configuration; deployment stopped."
    exit 1
fi
if ! printf '%s' "$ACCOUNT_CONFIG" |
    ssh -o ControlPath="$CONTROL_PATH" $USER@$HOST \
        'set -e; umask 077; mkdir -p "$HOME/.config/react-python-sqlite-boilerplate"; chmod 700 "$HOME/.config/react-python-sqlite-boilerplate"; temp=$(mktemp "$HOME/.config/react-python-sqlite-boilerplate/session.XXXXXX"); trap '\''rm -f "$temp"'\'' EXIT; cat > "$temp"; chmod 600 "$temp"; mv "$temp" "$HOME/.config/react-python-sqlite-boilerplate/session.json"'; then
    echo "Failed to install private account configuration; deployment stopped."
    exit 1
fi
unset ACCOUNT_CONFIG

echo "📦 Deploying backend to ${REMOTE_DIR}backend/..."
echo "🗂️  Ensuring remote directories exist..."

# Parse --force-fresh flag
FORCE_FRESH=0
for arg in "$@"; do
    if [ "$arg" = "--force-fresh" ]; then
        FORCE_FRESH=1
    fi
done

# Check if backend directory exists before creating (to detect first-time deploy)
BACKEND_EXISTS=$(ssh -o ControlPath="$CONTROL_PATH" $USER@$HOST "if [ -d '${REMOTE_DIR}backend' ]; then echo 1; else echo 0; fi")
ssh -o ControlPath="$CONTROL_PATH" $USER@$HOST "mkdir -p ${REMOTE_DIR}backend ${REMOTE_DIR}frontend ${REMOTE_DIR}shared"
echo "✅ Remote directories ensured."
# Rsync backend with --delete to remove unwanted files
rsync -avz --delete --progress \
    -e "ssh -o ControlPath=$CONTROL_PATH" \
    --exclude='api.cgi' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='database/*.db' \
    --exclude='database/*.db-journal' \
    --exclude='venv/' \
    --exclude='.venv/' \
    --exclude='*.log' \
    --exclude='logs/' \
    --exclude='.git/' \
    --exclude='.DS_Store' \
    backend/ $USER@$HOST:${REMOTE_DIR}backend/

# Files used by both backend (read at runtime) and frontend (bundled at build),
# e.g. LLM prompts/schemas. shared/.htaccess blocks direct web access.
rsync -avz --delete --progress \
    -e "ssh -o ControlPath=$CONTROL_PATH" \
    --exclude='.DS_Store' \
    shared/ $USER@$HOST:${REMOTE_DIR}shared/
ssh -o ControlPath="$CONTROL_PATH" $USER@$HOST "chmod -R g-w,o-w ${REMOTE_DIR}shared || true"

# The CGI launcher resolves its adjacent virtualenv and Python entry point.
scp -o ControlPath="$CONTROL_PATH" backend/api.cgi $USER@$HOST:${REMOTE_DIR}backend/api.cgi

# Harden permissions: remove group/world write bits on backend files and
# ensure CGI scripts are executable. Keep logs directory writable so the
# app can write logs (we create defaults only if missing).
echo "🔐 Hardening permissions on remote backend files..."
ssh -o ControlPath="$CONTROL_PATH" $USER@$HOST "chmod -R g-w,o-w ${REMOTE_DIR}backend || true; find ${REMOTE_DIR}backend -type f -name '*.cgi' -exec chmod 755 {} \; -o -name '*.py' -exec chmod 644 {} \; || true"
echo "✅ Remote permissions hardened."


# If backend did not exist before, run pip install and init_db.py

# Always ensure backend venv exists and dependencies are installed
echo "🐍 Setting up Python virtual environment and installing backend dependencies..."
if ! ssh -o ControlPath="$CONTROL_PATH" $USER@$HOST "cd '${REMOTE_DIR}backend' && bash -s" <<'REMOTE_PYTHON_SETUP'
set -e
if [ -d venv ]; then
    if ! venv/bin/python -c 'import sys, pip; from pathlib import Path; config = dict(line.strip().split(" = ", 1) for line in Path("venv/pyvenv.cfg").read_text().splitlines() if " = " in line); assert config["version"].split(".")[:2] == [str(sys.version_info.major), str(sys.version_info.minor)]'; then
        backup=$(mktemp -d venv-backup.XXXXXX)
        mv venv "$backup/venv"
        echo "Preserved incompatible virtualenv in $backup/venv; creating a new one."
    fi
fi
if [ ! -d venv ]; then
    python3 -m venv venv
fi
venv/bin/python -m pip install --upgrade pip
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -c 'import flask, flask_cors, flask_limiter, flask_wtf, dotenv'
REMOTE_PYTHON_SETUP
then
    echo "Backend dependency setup failed; deployment stopped."
    exit 1
fi
echo "✅ Python virtual environment and dependencies ready."

# If backend did not exist before, or --force-fresh is set, initialize the database using the venv's python
if [ "$BACKEND_EXISTS" = "0" ] || [ "$FORCE_FRESH" = "1" ]; then
    echo "🛠️  Backend setup: removing old database and initializing database (fresh deploy or --force-fresh)..."
    ssh -o ControlPath="$CONTROL_PATH" $USER@$HOST "cd ${REMOTE_DIR}backend && rm -f database/app.db"
    ssh -o ControlPath="$CONTROL_PATH" $USER@$HOST "cd ${REMOTE_DIR}backend && source venv/bin/activate && if [ -f init_db.py ]; then venv/bin/python init_db.py; fi"
    echo "✅ Backend initialized."
fi

# Apply additive account migrations on every deployment, including existing databases.
if ! ssh -o ControlPath="$CONTROL_PATH" $USER@$HOST "cd '${REMOTE_DIR}backend' && venv/bin/python -c 'import sqlite3; from account_schema import ensure_account_schema; db = sqlite3.connect(\"database/app.db\"); ensure_account_schema(db); db.close()'"; then
    echo "Database migration failed; deployment stopped."
    exit 1
fi

# Always ensure correct permissions for database directory and file
echo "🔑 Setting permissions for database directory and app.db..."
ssh -o ControlPath="$CONTROL_PATH" $USER@$HOST "chmod 775 ${REMOTE_DIR}backend/database || true"
ssh -o ControlPath="$CONTROL_PATH" $USER@$HOST "chmod 664 ${REMOTE_DIR}backend/database/app.db || true"
echo "✅ Permissions set."

echo ""
echo "🔧 Setting CGI permissions..."
ssh -o ControlPath="$CONTROL_PATH" $USER@$HOST "if [ -f ${REMOTE_DIR}backend/api.cgi ]; then chmod 755 ${REMOTE_DIR}backend/api.cgi || true; fi"
ssh -o ControlPath="$CONTROL_PATH" $USER@$HOST "if [ -f ${REMOTE_DIR}backend/.htaccess ]; then chmod 644 ${REMOTE_DIR}backend/.htaccess || true; fi"


# Ensure remote backend logs directory exists (but do not overwrite or touch log files)
echo "\n📝 Ensuring remote backend/logs/ directory exists (logs not overwritten)..."
ssh -o ControlPath="$CONTROL_PATH" $USER@$HOST "mkdir -p ${REMOTE_DIR}backend/logs && chmod 755 ${REMOTE_DIR}backend/logs || true"
# Create default log files only if they do not already exist (do not overwrite existing logs)
ssh -o ControlPath="$CONTROL_PATH" $USER@$HOST "for f in flask_error.log cgi_error.log import_time.log nosebeep_api_logs.txt; do if [ ! -f ${REMOTE_DIR}backend/logs/\$f ]; then touch ${REMOTE_DIR}backend/logs/\$f && chmod 664 ${REMOTE_DIR}backend/logs/\$f || true; fi; done"
echo "✅ Remote logs directory ensured (existing logs preserved)."

echo ""
echo "📦 Deploying frontend source to ${REMOTE_DIR}frontend/..."
rsync -avz --progress \
    -e "ssh -o ControlPath=$CONTROL_PATH" \
    --exclude='node_modules/' \
    --exclude='dist/' \
    --exclude='dist-ssr/' \
    --exclude='.git/' \
    --exclude='.DS_Store' \
    --exclude='*.local' \
    frontend/ $USER@$HOST:${REMOTE_DIR}frontend/

echo ""
echo "\n🏗️  Deploying built frontend to $REMOTE_DIR (web root)..."
echo "🔁 Generating frontend config and building frontend locally (to populate frontend/dist)"
# Generate config for frontend (ensures BASE_URL matches .env)
node frontend/generate-config.js

# Install frontend deps if missing (fast path)
if [ ! -d "frontend/node_modules" ]; then
    echo "📥 Installing frontend dependencies (first-time)..."
    (cd frontend && npm install)
fi

echo "🏗 Building frontend..."
if (cd frontend && npm run build); then
    echo "✅ Frontend built. Deploying dist to $REMOTE_DIR"
     rsync -avz --progress \
        -e "ssh -o ControlPath=$CONTROL_PATH" \
        frontend/dist/ $USER@$HOST:$REMOTE_DIR/
    echo "✅ Frontend deployed to $REMOTE_DIR"
else
    echo "⚠️  Frontend build failed — skipping dist deploy"
fi

# Ensure .htaccess for SPA routing is deployed with the built frontend
if [ -f frontend/.htaccess ]; then
    scp -o ControlPath="$CONTROL_PATH" frontend/.htaccess $USER@$HOST:${REMOTE_DIR}.htaccess
    echo "✅ .htaccess for SPA routing deployed to $REMOTE_DIR.htaccess"
fi

echo ""
echo "✅ Deployment complete!"
echo ""
echo "All files deployed to: $REMOTE_DIR"
# DOMAIN is already set from .env
# Ensure we print HTTP URLs for the CGI endpoints (convert https -> http if present)
DOMAIN_HTTP="${DOMAIN/#https:\/\//http://}"
if [ -z "$SUB_DIR" ]; then
    echo "Web root: $DOMAIN_HTTP/"
    echo "Backend API: $DOMAIN_HTTP/backend/api.cgi/"
else
    echo "Web root: $DOMAIN_HTTP/$SUB_DIR"
    echo "Backend API: $DOMAIN_HTTP/${SUB_DIR}backend/api.cgi/"
fi
