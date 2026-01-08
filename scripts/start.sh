#!/usr/bin/env sh
set -e

# Ensure PORT has a sensible default for local/dev use.
PORT="${PORT:-8000}"

exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
