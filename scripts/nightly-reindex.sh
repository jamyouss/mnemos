#!/bin/bash
# Nightly safety-net reindex, driven by launchd.
#
# The post-merge / post-checkout hooks keep the index fresh on pull and branch
# switch. This catches what they cannot see: rebases, resets, stashes, files
# edited outside git, and repos cloned while the server was down.
#
# Install:   ./scripts/install-nightly-reindex.sh
# Logs:      ~/Library/Logs/mnemos-reindex.log
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MNEMOS_URL="${MNEMOS_URL:-http://localhost:8100}"
LOCK="/tmp/mnemos-nightly-reindex.lock"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# Never let two runs overlap — a reindex can outlive its 24h window.
if ! mkdir "$LOCK" 2>/dev/null; then
    log "another reindex is still running (lock: $LOCK) — skipping"
    exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# The laptop may be off the dock, docker may be down, the stack may be stopped.
# None of that is an error worth alerting on.
if ! curl -sf -m 10 "$MNEMOS_URL/health" >/dev/null 2>&1; then
    log "mnemos unreachable at $MNEMOS_URL — skipping"
    exit 0
fi

PYTHON="$REPO_ROOT/venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
[ -n "$PYTHON" ] || { log "no python3 found — skipping"; exit 0; }

log "starting reindex (python=$PYTHON)"
MNEMOS_URL="$MNEMOS_URL" "$PYTHON" "$REPO_ROOT/scripts/reindex-all.py" --workers 4
STATUS=$?
log "reindex-all.py exited with $STATUS"

exit 0
