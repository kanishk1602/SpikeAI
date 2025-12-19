#!/usr/bin/env bash
set -e

# This script creates .venv at repo root, installs deps, and launches server on port 8080
# It must run without manual intervention for the evaluator.

VENV_DIR=".venv"

if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv $VENV_DIR
fi

# Activate and install fast
source $VENV_DIR/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

# sanity check credentials.json exists (evaluators will replace it; if missing, warn but continue)
if [ ! -f credentials.json ]; then
  echo "Warning: credentials.json not found at repo root. Create one or evaluator will replace it."
fi

# Start the app in background
# Use uvicorn for production like start. Redirect logs to server.log
uvicorn main:app --host 0.0.0.0 --port 8080 --workers 1 > server.log 2>&1 &

echo "Server launched (pid $!). Logs: server.log"
