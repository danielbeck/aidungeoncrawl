#!/bin/sh

# Shared hosting reliably launches shell CGI scripts, unlike Python shebangs.
backend_dir=$(dirname -- "${SCRIPT_FILENAME:-$0}") || exit 1
exec "$backend_dir/venv/bin/python" "$backend_dir/cgi_app.py"
