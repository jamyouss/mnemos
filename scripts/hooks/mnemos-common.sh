#!/bin/sh
# Shared logic for mnemos git hooks.
# Sourced by post-commit and pre-push hooks.

# Machine-local settings, written by install-hooks.sh. Needed because git
# hooks fire from GUI clients and IDEs that never read a shell profile, so an
# exported variable alone is not a reliable channel.
#
# Precedence is environment > settings file > built-in default. Sourcing a
# file of plain assignments would invert that, so the environment is saved
# across the source and put back afterwards.
_mnemos_env_url="${MNEMOS_URL:-}"
_mnemos_env_root="${MNEMOS_CODEBASE_ROOT:-}"
_mnemos_env_trigger="${MNEMOS_HOOK_TRIGGER:-}"

if [ -f "${MNEMOS_CONFIG_FILE:-$HOME/.config/mnemos/config}" ]; then
    . "${MNEMOS_CONFIG_FILE:-$HOME/.config/mnemos/config}"
fi

[ -n "$_mnemos_env_url" ] && MNEMOS_URL="$_mnemos_env_url"
[ -n "$_mnemos_env_root" ] && MNEMOS_CODEBASE_ROOT="$_mnemos_env_root"
[ -n "$_mnemos_env_trigger" ] && MNEMOS_HOOK_TRIGGER="$_mnemos_env_trigger"
unset _mnemos_env_url _mnemos_env_root _mnemos_env_trigger

MNEMOS_URL="${MNEMOS_URL:-http://localhost:8100}"
MNEMOS_HOOK_TRIGGER="${MNEMOS_HOOK_TRIGGER:-pre-push}"
# Two lists, two costs:
#   repos        memory extraction — one LLM call per push, so keep it to the
#                repos where real decisions get made.
#   index-repos  code indexing — cheap, so it can cover everything you want to
#                be able to search. Falls back to `repos` when absent.
MNEMOS_REPOS_CONFIG="${MNEMOS_REPOS_CONFIG:-$HOME/.config/mnemos/repos}"
MNEMOS_INDEX_REPOS_CONFIG="${MNEMOS_INDEX_REPOS_CONFIG:-$HOME/.config/mnemos/index-repos}"

# Return 0 when the current repo sits at, or under, one of the roots listed
# in the config file passed as $1.
_mnemos_repo_listed_in() {
    _cfg="$1"
    [ -f "$_cfg" ] || return 1

    _repo="$(git rev-parse --show-toplevel 2>/dev/null || echo "")"
    [ -n "$_repo" ] || return 1

    while IFS= read -r _listed || [ -n "$_listed" ]; do
        # Skip comments and empty lines
        case "$_listed" in
            "#"*|"") continue ;;
        esac
        # Expand ~
        _listed=$(eval echo "$_listed")
        _listed="${_listed%/}"
        # Anchored on a path boundary so /dev never matches /dev-scratch.
        case "$_repo" in
            "$_listed"|"$_listed"/*) return 0 ;;
        esac
    done < "$_cfg"

    return 1
}

# Is this repo watched for memory extraction?
mnemos_is_watched_repo() {
    _mnemos_repo_listed_in "$MNEMOS_REPOS_CONFIG"
}

# Is this repo watched for code indexing?
mnemos_is_indexed_repo() {
    if [ -f "$MNEMOS_INDEX_REPOS_CONFIG" ]; then
        _mnemos_repo_listed_in "$MNEMOS_INDEX_REPOS_CONFIG"
    else
        mnemos_is_watched_repo
    fi
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

# ---------------------------------------------------------------------------
# Incremental code indexing (post-merge / post-checkout)
# ---------------------------------------------------------------------------

# Host directory bind-mounted at /data/codebase inside the mnemos container.
# Must match MNEMOS_CODEBASE_HOST_PATH in the server's .env — export
# MNEMOS_CODEBASE_ROOT from your shell profile when yours is not ~/code.
MNEMOS_CODEBASE_ROOT="${MNEMOS_CODEBASE_ROOT:-$HOME/code}"
MNEMOS_CODEBASE_ROOT="${MNEMOS_CODEBASE_ROOT%/}"

# Above this many changed files, one bulk /api/reindex beats N round-trips.
MNEMOS_MAX_INCREMENTAL_FILES="${MNEMOS_MAX_INCREMENTAL_FILES:-200}"

# Files larger than this are not worth embedding (lockfiles, generated blobs).
MNEMOS_MAX_FILE_BYTES="${MNEMOS_MAX_FILE_BYTES:-1048576}"

# Translate a host path into its container path. Fails (returns 1) when the
# path lies outside the indexed codebase root.
mnemos_container_path() {
    case "$1" in
        "$MNEMOS_CODEBASE_ROOT"/*) ;;
        *) return 1 ;;
    esac
    printf '/data/codebase/%s\n' "${1#"$MNEMOS_CODEBASE_ROOT"/}"
}

# Push one file to the index. Tags are resolved server-side from
# config/projects.yaml based on the container path, so none are sent here.
mnemos_push_file() {
    _host="$1"
    _container="$2"

    [ -f "$_host" ] || return 0

    # Skip oversized files and anything that is not valid text.
    _size=$(wc -c < "$_host" 2>/dev/null | tr -d ' ')
    [ -n "$_size" ] && [ "$_size" -gt "$MNEMOS_MAX_FILE_BYTES" ] && return 0
    grep -Iq . "$_host" 2>/dev/null || return 0

    jq -n \
        --arg fp "$_container" \
        --rawfile c "$_host" \
        '{collection: "mnemos_code", file_path: $fp, content: $c}' 2>/dev/null \
    | curl -s -X POST "$MNEMOS_URL/api/index" \
        -H "Content-Type: application/json" \
        --data-binary @- >/dev/null 2>&1
}

mnemos_delete_file() {
    curl -s -X DELETE "$MNEMOS_URL/api/index/mnemos_code$1" >/dev/null 2>&1
}

# Reindex everything under one repo in a single background server task.
mnemos_bulk_reindex() {
    jq -n --arg p "$1" \
        '{collection: "mnemos_code", path: $p, full: true, workers: 4}' 2>/dev/null \
    | curl -s -X POST "$MNEMOS_URL/api/reindex" \
        -H "Content-Type: application/json" \
        --data-binary @- >/dev/null 2>&1
}

# Index every file that changed between two revisions.
# Usage: mnemos_index_range <from_rev> <to_rev>
# Detached and fail-safe — never blocks or fails the git operation.
mnemos_index_range() {
    _from="$1"
    _to="$2"

    [ -n "$_from" ] && [ -n "$_to" ] || return 0
    [ "$_from" = "$_to" ] && return 0

    _repo_root="$(git rev-parse --show-toplevel 2>/dev/null || echo "")"
    [ -n "$_repo_root" ] || return 0

    _repo_container="$(mnemos_container_path "$_repo_root")" || return 0

    # --no-renames keeps the status vocabulary to A/M/D, so a rename becomes a
    # delete plus an add and the stale chunks of the old path are dropped.
    _changed="$(git diff --name-status --no-renames "$_from" "$_to" 2>/dev/null || echo "")"
    [ -n "$_changed" ] || return 0

    (
        _count=$(printf '%s\n' "$_changed" | wc -l | tr -d ' ')
        if [ "$_count" -gt "$MNEMOS_MAX_INCREMENTAL_FILES" ]; then
            mnemos_bulk_reindex "$_repo_container"
            exit 0
        fi

        printf '%s\n' "$_changed" | while IFS="$(printf '\t')" read -r _status _path; do
            [ -n "$_path" ] || continue
            _cpath="$(mnemos_container_path "$_repo_root/$_path")" || continue
            case "$_status" in
                D*) mnemos_delete_file "$_cpath" ;;
                *)  mnemos_push_file "$_repo_root/$_path" "$_cpath" ;;
            esac
        done
    ) >/dev/null 2>&1 &
}
