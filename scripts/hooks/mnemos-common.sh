#!/bin/sh
# Shared logic for mnemos git hooks.
# Sourced by post-commit and pre-push hooks.

MNEMOS_URL="${MNEMOS_URL:-http://localhost:8100}"
MNEMOS_HOOK_TRIGGER="${MNEMOS_HOOK_TRIGGER:-pre-push}"
MNEMOS_REPOS_CONFIG="$HOME/.config/mnemos/repos"

# Check if the current repo is in the watched paths list.
# Returns 0 if watched, 1 if not.
mnemos_is_watched_repo() {
    if [ ! -f "$MNEMOS_REPOS_CONFIG" ]; then
        return 1
    fi

    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "")"
    if [ -z "$REPO_ROOT" ]; then
        return 1
    fi

    while IFS= read -r watched || [ -n "$watched" ]; do
        # Skip comments and empty lines
        case "$watched" in
            "#"*|"") continue ;;
        esac
        # Expand ~
        watched=$(eval echo "$watched")
        case "$REPO_ROOT" in
            "$watched"*) return 0 ;;
        esac
    done < "$MNEMOS_REPOS_CONFIG"

    return 1
}

# Chain to repo-local hook if it exists.
# Usage: mnemos_chain_local "pre-push" "$@"
mnemos_chain_local() {
    HOOK_NAME="$1"
    shift
    REPO_HOOK="$(git rev-parse --git-dir)/hooks/$HOOK_NAME"
    if [ -x "$REPO_HOOK" ]; then
        "$REPO_HOOK" "$@" || exit $?
    fi
}

# Send extraction request to mnemos (fire and forget).
mnemos_extract() {
    COMMIT_MSG="$1"
    DIFF="$2"

    if [ -z "$DIFF" ]; then
        return
    fi

    curl -s -X POST "$MNEMOS_URL/api/memory/extract" \
        -H "Content-Type: application/json" \
        -d "$(jq -n \
            --arg msg "$COMMIT_MSG" \
            --arg diff "$DIFF" \
            '{commit_message: $msg, diff: $diff}')" \
        >/dev/null 2>&1 &
}
