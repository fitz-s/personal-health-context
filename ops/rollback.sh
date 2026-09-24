#!/bin/bash
# Roll the PROGRAM back to a previous git revision; optionally also roll the DATA back to a pre-migration snapshot.
#   ops/rollback.sh <git-rev>                                   program only; data untouched
#   ops/rollback.sh <git-rev> <pre-v*.sqlite3> <NEW_ROOT>        also build NEW_ROOT from that snapshot
# A newer schema than <git-rev> supports is refused (no downgrade). The pre-migration file is
# <root>/migrations/pre-vN-<ts>.sqlite3, written automatically before migration N. `phctx restore-premigration`
# (run by the CURRENT program, before checkout) copies it plus every original it references from the live root,
# hash-verified, into NEW_ROOT and checks integrity without migrating it. Afterwards point [app] root at NEW_ROOT;
# the old root is kept. Captures made after that migration exist only in the old root.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
REV="${1:?usage: $0 <git-rev> [<pre-v*.sqlite3> <NEW_ROOT>]}"
PRE="${2:-}"; NEW_ROOT="${3:-}"
[ -z "$PRE" ] || [ -n "$NEW_ROOT" ] || { echo "usage: $0 <git-rev> [<pre-v*.sqlite3> <NEW_ROOT>]" >&2; exit 2; }
cd "$REPO"
[ -z "$(git status --porcelain -- src contracts prompts)" ] || { echo "uncommitted changes in src/contracts/prompts; commit or stash first" >&2; exit 1; }
# Rebuild the data first (it only reads the live root): a failed restore leaves services and program untouched.
if [ -n "$PRE" ]; then
  PYTHONPATH="$REPO/src" "$REPO/.venv/bin/python" -m phctx restore-premigration "$PRE" --new-root "$NEW_ROOT"
fi
"$REPO/ops/uninstall.sh"
git checkout "$REV" -- src contracts prompts pyproject.toml uv.lock
uv sync -q
if [ -n "$PRE" ]; then
  echo "Set [app] root = \"$NEW_ROOT\" in ${PHCTX_CONFIG:-$HOME/.config/phctx/config.toml}, then re-run ops/install.sh."
  echo "Program rolled back to $REV; data rebuilt at $NEW_ROOT (old root kept)."
  exit 0
fi
PYTHONPATH="$REPO/src" "$REPO/.venv/bin/python" -m phctx doctor || true
"$REPO/ops/install.sh"
echo "Program rolled back to $REV. Data untouched."
