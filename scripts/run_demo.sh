#!/usr/bin/env bash
# One-shot demo runner. Starts the server; the server auto-spawns the log generator when SIMULATE=1.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "Missing .env — copy .env.example and set ANTHROPIC_API_KEY first."
  exit 1
fi

python -c "from app import kb; kb.seed_from_file()"
uvicorn app.main:app --host 0.0.0.0 --port 8000
