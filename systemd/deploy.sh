#!/usr/bin/env bash
#
# Auto-deploy: pull latest from GitHub and restart the bot only if something changed.
# Runs as root (via the assistant-deploy.service/timer); git operations run as the
# 'assistant' user so file ownership stays correct.
#
# The whole body is wrapped in braces on purpose. bash reads a script lazily, by
# byte offset - and this script git-pulls a new copy of ITSELF partway through.
# Without the braces, any commit that changes deploy.sh shifts the byte offsets
# under the running shell, which then resumes mid-line in the new file and does
# something arbitrary (usually: silently skips the restart). The braces force
# bash to parse the entire block before executing any of it.
{
set -euo pipefail

REPO="/home/assistant/telegram-assistant"
SERVICE="assistant"
cd "$REPO"

before="$(runuser -u assistant -- git rev-parse HEAD)"
runuser -u assistant -- git pull --quiet --ff-only
after="$(runuser -u assistant -- git rev-parse HEAD)"

# Sync submodules (vendor/last30days) to the commits this revision pins. Run on
# every pass, not just on change: it's a no-op when already in sync, and it
# self-heals a checkout where the submodule was never initialised.
#
# Deliberately non-fatal, and deliberately AFTER the restart decision below is
# reachable. Under `set -e` a failure here used to abort the run with HEAD
# already advanced, so the next pass saw "no changes" and skipped the restart
# permanently - one flaky clone silently pinned the bot to old code. A stale
# submodule degrades one tool; a skipped restart hides every other change.
sync_submodules() {
  if ! runuser -u assistant -- git submodule update --init --recursive --quiet; then
    echo "WARNING: submodule sync failed; /r research may be unavailable." >&2
  fi
}

if [ "$before" = "$after" ]; then
  sync_submodules
  echo "No changes (at ${after:0:8})."
  exit 0
fi

echo "Updated ${before:0:8} -> ${after:0:8}. Installing deps and restarting..."

sync_submodules

# Refresh dependencies in case requirements.txt changed (no-op if unchanged).
runuser -u assistant -- "$REPO/venv/bin/pip" install -q -r "$REPO/requirements.txt"

systemctl restart "$SERVICE"
echo "Deployed ${after:0:8} and restarted $SERVICE."
exit 0
}
