#!/bin/bash

# Development startup script - runs both frontend and backend

set -e

echo "🚀 Starting development servers..."
echo ""

# Start backend in background
echo "🐍 Starting backend on http://127.0.0.1:8000"
cd backend
DEBUG=1 python3 app.py &
BACKEND_PID=$!
cd ..

# Give backend a moment to start
sleep 2

# Start frontend (will run in foreground)
echo "⚛️  Starting frontend on http://localhost:3000"
echo ""
cd frontend

# Ensure config is generated with development settings
NODE_ENV=development node generate-config.js

# Start frontend dev server
npm run vite

# Cleanup: Kill backend when frontend exits
echo ""
echo "🛑 Shutting down servers..."
kill $BACKEND_PID 2>/dev/null || true
echo "✅ Servers stopped"
