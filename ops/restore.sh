#!/bin/bash
# Restore a snapshot dir or encrypted .phbk into a NEW root (never over live data), verify, and print counts.
#   ops/restore.sh SNAPSHOT_OR_ARCHIVE NEW_ROOT
# To switch to it: stop services (ops/uninstall.sh), point [app] root at NEW_ROOT, reinstall. Old root is kept.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
[ $# -eq 2 ] || { echo "usage: $0 SNAPSHOT_OR_ARCHIVE NEW_ROOT" >&2; exit 2; }
PYTHONPATH="$REPO/src" exec "$REPO/.venv/bin/python" -m phctx restore "$1" --new-root "$2"
