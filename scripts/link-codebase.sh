#!/usr/bin/env bash
# Symlink your real source-code directory into ./data/codebase so the default
# docker-compose mount picks it up — without having to edit docker-compose.yml.
#
# Usage:
#     ./scripts/link-codebase.sh ~/code
#     ./scripts/link-codebase.sh /srv/projects/my-app  myapp
#
# The second argument (optional) is the symlink name. Defaults to the basename
# of the source dir.
set -euo pipefail

if [ $# -lt 1 ]; then
  cat >&2 <<'EOF'
Usage: ./scripts/link-codebase.sh <host-path> [link-name]

Examples:
    ./scripts/link-codebase.sh ~/code                       # ./data/codebase/code     → ~/code
    ./scripts/link-codebase.sh ~/Developments/Projects org  # ./data/codebase/org      → ~/Developments/Projects
    ./scripts/link-codebase.sh ~/code/my-monorepo my-app    # ./data/codebase/my-app   → ~/code/my-monorepo
EOF
  exit 1
fi

SOURCE="$1"
NAME="${2:-$(basename "$SOURCE")}"
TARGET_DIR="$(cd "$(dirname "$0")/.." && pwd)/data/codebase"
TARGET="${TARGET_DIR}/${NAME}"

# Expand ~ and verify the source exists
SOURCE_EXPANDED="${SOURCE/#\~/$HOME}"
if [ ! -d "$SOURCE_EXPANDED" ]; then
  echo "✗ Source directory does not exist: $SOURCE_EXPANDED" >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"

if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
  echo "→ Replacing existing $TARGET"
  rm -rf "$TARGET"
fi

ln -s "$SOURCE_EXPANDED" "$TARGET"
echo "✓ Linked $TARGET → $SOURCE_EXPANDED"
echo ""
echo "Inside the rag-server container this appears as:"
echo "    /data/codebase/${NAME}"
echo ""
echo "Reindex it with:"
echo "    mnemos reindex --recreate --full --collection mnemos_code \\"
echo "                   --tags ${NAME} --path /data/codebase/${NAME}"
