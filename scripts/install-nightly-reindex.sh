#!/bin/bash
# Install (or reinstall) the nightly reindex launchd agent.
#
# Usage:
#   ./scripts/install-nightly-reindex.sh            # every night at 04:00
#   ./scripts/install-nightly-reindex.sh --hour 3
#   ./scripts/install-nightly-reindex.sh --weekly   # Sundays only
#   ./scripts/install-nightly-reindex.sh --uninstall
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.mnemos.reindex"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/mnemos-reindex.log"
HOUR=4
WEEKLY=false
UNINSTALL=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --hour) HOUR="$2"; shift 2 ;;
        --weekly) WEEKLY=true; shift ;;
        --uninstall) UNINSTALL=true; shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# launchctl bootout is the documented way to remove an agent; it errors when
# nothing is loaded, which is fine on a first install.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true

if [ "$UNINSTALL" = true ]; then
    rm -f "$PLIST"
    echo "Uninstalled $LABEL"
    exit 0
fi

chmod +x "$REPO_ROOT/scripts/nightly-reindex.sh"
mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"

if [ "$WEEKLY" = true ]; then
    SCHEDULE="        <key>Weekday</key><integer>0</integer>
        <key>Hour</key><integer>$HOUR</integer>
        <key>Minute</key><integer>0</integer>"
else
    SCHEDULE="        <key>Hour</key><integer>$HOUR</integer>
        <key>Minute</key><integer>0</integer>"
fi

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$REPO_ROOT/scripts/nightly-reindex.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
$SCHEDULE
    </dict>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>$LOG</string>
    <key>StandardErrorPath</key>
    <string>$LOG</string>
    <key>ProcessType</key>
    <string>Background</string>
    <key>LowPriorityIO</key>
    <true/>
    <key>Nice</key>
    <integer>10</integer>
</dict>
</plist>
EOF

plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "Installed $LABEL"
if [ "$WEEKLY" = true ]; then
    echo "  Schedule: Sundays at ${HOUR}:00"
else
    echo "  Schedule: daily at ${HOUR}:00"
fi
echo "  Log:      $LOG"
echo ""
echo "Run it now:   launchctl kickstart -p gui/$(id -u)/$LABEL"
echo "Uninstall:    ./scripts/install-nightly-reindex.sh --uninstall"
