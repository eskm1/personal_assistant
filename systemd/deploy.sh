#!/usr/bin/env bash
#
# Auto-deploy: pull latest from GitHub and restart the bot only if something changed.
# Runs as root (via the assistant-deploy.service/timer); git operations run as the
# 'assistant' user so file ownership stays correct.
#
set -euo pipefail

REPO="/home/assistant/telegram-assistant"
SERVICE="assistant"
cd "$REPO"

before="$(runuser -u assistant -- git rev-parse HEAD)"
runuser -u assistant -- git pull --quiet --ff-only
after="$(runuser -u assistant -- git rev-parse HEAD)"

if [ "$before" = "$after" ]; then
  echo "No changes (at ${after:0:8})."
  exit 0
fi

echo "Updated ${before:0:8} -> ${after:0:8}. Installing deps and restarting..."

# Refresh dependencies in case requirements.txt changed (no-op if unchanged).
runuser -u assistant -- "$REPO/venv/bin/pip" install -q -r "$REPO/requirements.txt"

systemctl restart "$SERVICE"
echo "Deployed ${after:0:8} and restarted $SERVICE."
