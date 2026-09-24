#!/bin/bash
# Roll the PROGRAM back to a previous git revision without touching data.
#   ops/rollback.sh <git-rev>
# If the data schema is newer than that revision supports, phctx refuses to open it (no downgrade); then
# restore the pre-upgrade snapshot (root/migrations/pre-v*.sqlite3 or a backup) into a NEW root with
# ops/restore.sh and point config at it.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
REV="${1:?usage: $0 <git-rev>}"
cd "$REPO"
[ -z "$(git status --porcelain -- src contracts prompts)" ] || { echo "uncommitted changes in src/contracts/prompts; commit or stash first" >&2; exit 1; }
"$REPO/ops/uninstall.sh"
git checkout "$REV" -- src contracts prompts pyproject.toml uv.lock
uv sync -q
PYTHONPATH="$REPO/src" "$REPO/.venv/bin/python" -m phctx doctor || true
"$REPO/ops/install.sh"
echo "Program rolled back to $REV. Data untouched."
