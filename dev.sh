#!/bin/bash

# Development startup script - runs both frontend and backend

set -e

cd "$(dirname "$0")"

stop_port() {
    local port="$1"
    local pids
    local pid
    local attempt
    pids=$(lsof -nP -t -iTCP:"$port" -sTCP:LISTEN | sort -u)
    if [ -z "$pids" ]; then
        return
    fi
    echo "Stopping existing listeners on port $port: $pids"
    for pid in $pids; do
        if ! kill "$pid" 2>/dev/null && kill -0 "$pid" 2>/dev/null; then
            echo "Unable to stop process $pid on port $port." >&2
            return 1
        fi
    done
    for attempt in 1 2 3 4 5; do
        if ! lsof -nP -t -iTCP:"$port" -sTCP:LISTEN >/dev/null; then
            return
        fi
        sleep 1
    done
    echo "Port $port is still occupied; refusing to start another server." >&2
    return 1
}

cleanup() {
    if [ -n "${BACKEND_PID:-}" ]; then
        echo ""
        echo "Stopping backend process $BACKEND_PID..."
        kill "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
    fi
}

command -v lsof >/dev/null || { echo "lsof is required to check dev ports." >&2; exit 1; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "🚀 Starting development servers..."
echo ""

stop_port 8000
stop_port 3000

# Start backend in background
echo "🐍 Starting backend on http://127.0.0.1:8000"
cd backend
DEBUG=1 python3 app.py &
BACKEND_PID=$!
cd ..

# Give backend a moment to start
sleep 2
if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "Backend failed to start." >&2
    exit 1
fi

# Start frontend (will run in foreground)
echo "⚛️  Starting frontend on http://localhost:3000"
echo ""
cd frontend

# Ensure config is generated with development settings
NODE_ENV=development node generate-config.js

# Start frontend dev server
npm run vite
