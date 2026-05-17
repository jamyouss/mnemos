#!/usr/bin/env bash
# Mnemos quickstart demo: index the tiny `examples/quickstart/` repo bundled
# with this checkout, then run two illustrative searches. Lets a new user
# verify their setup end-to-end in ~30 seconds, without needing to plug
# their own code in first.
set -euo pipefail

MNEMOS_URL="${MNEMOS_URL:-http://localhost:8100}"
EXAMPLE_DIR="$(cd "$(dirname "$0")/.." && pwd)/examples/quickstart"

if [ ! -d "$EXAMPLE_DIR" ]; then
  echo "✗ examples/quickstart not found at $EXAMPLE_DIR" >&2
  exit 1
fi

# 1. Check the server is up
if ! curl -sf "${MNEMOS_URL}/health" >/dev/null; then
  cat >&2 <<EOF
✗ Mnemos server not reachable at ${MNEMOS_URL}.
  Start it first:
      make up           # or:  docker compose up -d
EOF
  exit 1
fi

# IMPORTANT: this only works if the examples/quickstart path is visible
# inside the rag-server container. With the default compose mount of
# ./data/codebase, we expect users to symlink or copy examples there.
# For convenience the demo expects a /data/codebase/quickstart-demo mount.
CONTAINER_PATH="/data/codebase/quickstart-demo"

echo "==> Indexing the bundled example repo into mnemos_code…"
echo "    (expects ${CONTAINER_PATH} inside the rag-server container —"
echo "     symlink or bind-mount ${EXAMPLE_DIR} there before running)"
RESP=$(curl -s -X POST "${MNEMOS_URL}/api/reindex" \
  -H "Content-Type: application/json" \
  -d "{
    \"collection\":\"mnemos_code\",
    \"full\":true,
    \"recreate\":false,
    \"workers\":1,
    \"path\":\"${CONTAINER_PATH}\",
    \"project\":\"quickstart-demo\"
  }")
echo "    $RESP"

echo ""
echo "==> Waiting for indexing to settle (max 30s)…"
for _ in $(seq 1 15); do
  sleep 2
  COUNT=$(curl -s "${MNEMOS_URL}/api/status" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['collections'].get('mnemos_code',{}).get('points_count',0))")
  if [ "$COUNT" -gt 0 ]; then
    echo "    indexed $COUNT chunks."
    break
  fi
done

echo ""
echo "==> Sample search: 'how is the user created?'"
curl -s -X POST "${MNEMOS_URL}/api/search-code" \
  -H "Content-Type: application/json" \
  -d '{"query":"how is the user created?","project":"quickstart-demo","limit":3}' \
  | python3 -c '
import json, sys
d = json.load(sys.stdin)
for i, r in enumerate(d.get("results", []), 1):
    proj = (r.get("metadata") or {}).get("project") or "-"
    print(f"  {i}. project={proj!r:20s} score={r[\"score\"]:.3f}")
    print(f"     {r[\"file_path\"]}")
'

echo ""
echo "Done. Plug your own code by editing docker-compose.yml's bind mount"
echo "or by setting MNEMOS_CODEBASE_HOST_PATH=~/code and \`make up\`."
