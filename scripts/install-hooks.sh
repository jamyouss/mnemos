#!/bin/bash
# Install Mnemos global git hooks.
#
# Usage:
#   ./install-hooks.sh --global --watch ~/code/your-org
#   ./install-hooks.sh --global --watch /path/to/repo --trigger both
#
# Options:
#   --global              Install hooks globally via core.hooksPath
#   --watch <path>        Add a path to the memory-extraction list (~/.config/mnemos/repos)
#   --watch-index <path>  Add a path to the indexing list (~/.config/mnemos/index-repos)
#   --codebase-root <path>  Host dir bind-mounted at /data/codebase (default: ~/code).
#                           Persisted to ~/.config/mnemos/config so hooks launched
#                           from GUI clients and IDEs pick it up too.
#   --trigger <mode>      Set MNEMOS_HOOK_TRIGGER (pre-push|post-commit|both, default: pre-push)
#
# Installed hooks:
#   post-commit / pre-push    memory extraction (gated by MNEMOS_HOOK_TRIGGER)
#   post-merge / post-checkout  incremental code indexing (always on for watched repos)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_SRC="$SCRIPT_DIR/hooks"
GLOBAL_HOOKS_DIR="$HOME/.config/git/hooks"
MNEMOS_CONFIG_DIR="$HOME/.config/mnemos"
REPOS_CONFIG="$MNEMOS_CONFIG_DIR/repos"
INDEX_REPOS_CONFIG="$MNEMOS_CONFIG_DIR/index-repos"
SETTINGS_CONFIG="$MNEMOS_CONFIG_DIR/config"
CODEBASE_ROOT=""
GLOBAL=false
WATCH_PATHS=()
WATCH_INDEX_PATHS=()
TRIGGER=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --global) GLOBAL=true; shift ;;
        --watch) WATCH_PATHS+=("$2"); shift 2 ;;
        --watch-index) WATCH_INDEX_PATHS+=("$2"); shift 2 ;;
        --codebase-root) CODEBASE_ROOT="$2"; shift 2 ;;
        --trigger) TRIGGER="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [ "$GLOBAL" = false ]; then
    echo "Usage: $0 --global --watch <path> [--watch-index <path>] [--trigger pre-push|post-commit|both]"
    exit 1
fi

# 1. Copy hooks to global hooks directory
mkdir -p "$GLOBAL_HOOKS_DIR"
for hook in post-commit pre-push post-merge post-checkout mnemos-common.sh; do
    cp "$HOOKS_SRC/$hook" "$GLOBAL_HOOKS_DIR/$hook"
    chmod +x "$GLOBAL_HOOKS_DIR/$hook"
done
echo "Hooks installed to $GLOBAL_HOOKS_DIR"

# 2. Set global core.hooksPath
git config --global core.hooksPath "$GLOBAL_HOOKS_DIR"
echo "Set core.hooksPath to $GLOBAL_HOOKS_DIR"

# 3. Add watched paths to config
mkdir -p "$MNEMOS_CONFIG_DIR"
if [ ! -f "$REPOS_CONFIG" ]; then
    echo "# Mnemos memory-extraction paths (one per line)" > "$REPOS_CONFIG"
    echo "# post-commit / pre-push only extract memories for repos under these paths." >> "$REPOS_CONFIG"
fi
if [ ! -f "$INDEX_REPOS_CONFIG" ]; then
    echo "# Mnemos code-indexing paths (one per line)" > "$INDEX_REPOS_CONFIG"
    echo "# post-merge / post-checkout only index repos under these paths." >> "$INDEX_REPOS_CONFIG"
    echo "# Falls back to ./repos when this file is absent." >> "$INDEX_REPOS_CONFIG"
fi

add_path() {
    CONFIG_FILE="$1"
    LABEL="$2"
    # Resolve to absolute path
    ABS_PATH="$(cd "$3" 2>/dev/null && pwd || echo "$3")"
    if grep -qxF "$ABS_PATH" "$CONFIG_FILE" 2>/dev/null; then
        echo "Already in $LABEL list: $ABS_PATH"
    else
        echo "$ABS_PATH" >> "$CONFIG_FILE"
        echo "Added to $LABEL list: $ABS_PATH"
    fi
}

for wp in "${WATCH_PATHS[@]}"; do
    add_path "$REPOS_CONFIG" "memory" "$wp"
done

for wp in "${WATCH_INDEX_PATHS[@]}"; do
    add_path "$INDEX_REPOS_CONFIG" "indexing" "$wp"
done

# 4. Persist the codebase root
if [ -n "$CODEBASE_ROOT" ]; then
    ABS_ROOT="$(cd "$CODEBASE_ROOT" 2>/dev/null && pwd || echo "$CODEBASE_ROOT")"
    touch "$SETTINGS_CONFIG"
    # Rewrite in place so re-running the installer never stacks duplicates.
    grep -v '^MNEMOS_CODEBASE_ROOT=' "$SETTINGS_CONFIG" > "$SETTINGS_CONFIG.tmp" 2>/dev/null || true
    mv "$SETTINGS_CONFIG.tmp" "$SETTINGS_CONFIG"
    echo "MNEMOS_CODEBASE_ROOT=\"$ABS_ROOT\"" >> "$SETTINGS_CONFIG"
    echo "Codebase root set to $ABS_ROOT ($SETTINGS_CONFIG)"
fi

# 5. Set trigger if specified
if [ -n "$TRIGGER" ]; then
    echo ""
    echo "Add to your shell profile (~/.zshrc or ~/.bashrc):"
    echo "  export MNEMOS_HOOK_TRIGGER=$TRIGGER"
fi

echo ""
echo "Done. Mnemos hooks are now active globally."
echo "Memory extraction: $(grep -v '^#' "$REPOS_CONFIG" | grep -vc '^$') repo root(s)"
echo "Code indexing:     $(grep -v '^#' "$INDEX_REPOS_CONFIG" | grep -vc '^$') repo root(s)"
