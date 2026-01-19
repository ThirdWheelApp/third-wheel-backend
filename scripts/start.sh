#!/usr/bin/env sh
set -e

# Ensure PORT has a sensible default for local/dev use.
PORT="${PORT:-8000}"

# Enable debug logging to diagnose WebSocket connection issues
# --ws websockets: explicitly use websockets library (not wsproto)
# --log-level info: show connection-level logs
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --ws websockets --log-level info
