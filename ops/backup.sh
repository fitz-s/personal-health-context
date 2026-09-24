#!/bin/bash
# Consistent snapshot now; add --encrypt to also write the AES-GCM archive to [backup] cloud_dir.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHONPATH="$REPO/src" exec "$REPO/.venv/bin/python" -m phctx backup "$@"
