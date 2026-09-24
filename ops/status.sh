#!/bin/bash
# Operational status (no health content): launchd state, last worker runs, sources, backups.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
for label in com.personalhealthcontext.worker com.personalhealthcontext.ingest com.personalhealthcontext.backup com.personalhealthcontext.tunnel com.personalhealthcontext.tunnel-readonly; do
  if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
    st=$(launchctl print "gui/$(id -u)/$label" | awk -F'= ' '/last exit code/{print $2; exit}')
    echo "$label: loaded (last exit: ${st:-n/a})"
  else echo "$label: not loaded"; fi
done
PYTHONPATH="$REPO/src" "$REPO/.venv/bin/python" -m phctx status
