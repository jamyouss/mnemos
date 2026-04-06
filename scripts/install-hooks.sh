#!/bin/bash
# Install Mnemos global git hooks.
#
# Usage:
#   ./install-hooks.sh --global --watch ~/Developments/Projects/digital-gigafactory
#   ./install-hooks.sh --global --watch /path/to/repo --trigger both
#
# Options:
#   --global              Install hooks globally via core.hooksPath
#   --watch <path>        Add a path to the watched repos list
#   --trigger <mode>      Set MNEMOS_HOOK_TRIGGER (pre-push|post-commit|both, default: pre-push)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_SRC="$SCRIPT_DIR/hooks"
GLOBAL_HOOKS_DIR="$HOME/.config/git/hooks"
MNEMOS_CONFIG_DIR="$HOME/.config/mnemos"
REPOS_CONFIG="$MNEMOS_CONFIG_DIR/repos"
GLOBAL=false
WATCH_PATHS=()
TRIGGER=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --global) GLOBAL=true; shift ;;
        --watch) WATCH_PATHS+=("$2"); shift 2 ;;
        --trigger) TRIGGER="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [ "$GLOBAL" = false ]; then
    echo "Usage: $0 --global --watch <path> [--trigger pre-push|post-commit|both]"
    exit 1
fi

# 1. Copy hooks to global hooks directory
mkdir -p "$GLOBAL_HOOKS_DIR"
cp "$HOOKS_SRC/post-commit" "$GLOBAL_HOOKS_DIR/post-commit"
cp "$HOOKS_SRC/pre-push" "$GLOBAL_HOOKS_DIR/pre-push"
cp "$HOOKS_SRC/mnemos-common.sh" "$GLOBAL_HOOKS_DIR/mnemos-common.sh"
chmod +x "$GLOBAL_HOOKS_DIR/post-commit"
chmod +x "$GLOBAL_HOOKS_DIR/pre-push"
chmod +x "$GLOBAL_HOOKS_DIR/mnemos-common.sh"
echo "Hooks installed to $GLOBAL_HOOKS_DIR"

# 2. Set global core.hooksPath
git config --global core.hooksPath "$GLOBAL_HOOKS_DIR"
echo "Set core.hooksPath to $GLOBAL_HOOKS_DIR"

# 3. Add watched paths to config
mkdir -p "$MNEMOS_CONFIG_DIR"
if [ ! -f "$REPOS_CONFIG" ]; then
    echo "# Mnemos watched repository paths (one per line)" > "$REPOS_CONFIG"
    echo "# Hooks only trigger extraction for repos under these paths." >> "$REPOS_CONFIG"
fi

for wp in "${WATCH_PATHS[@]}"; do
    # Resolve to absolute path
    ABS_PATH="$(cd "$wp" 2>/dev/null && pwd || echo "$wp")"
    # Check if already in config
    if grep -qxF "$ABS_PATH" "$REPOS_CONFIG" 2>/dev/null; then
        echo "Already watched: $ABS_PATH"
    else
        echo "$ABS_PATH" >> "$REPOS_CONFIG"
        echo "Added to watch list: $ABS_PATH"
    fi
done

# 4. Set trigger if specified
if [ -n "$TRIGGER" ]; then
    echo ""
    echo "Add to your shell profile (~/.zshrc or ~/.bashrc):"
    echo "  export MNEMOS_HOOK_TRIGGER=$TRIGGER"
fi

echo ""
echo "Done. Mnemos hooks are now active globally."
echo "Watched repos: $(cat "$REPOS_CONFIG" | grep -v '^#' | grep -v '^$' | tr '\n' ', ')"
