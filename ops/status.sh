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
# ChatGPT file hosts refused and still not in [files] allowed_download_hosts (exact hosts only).
LOG="$HOME/Library/Logs/PersonalHealthContext/mcp-full.log"
[ -r "$LOG" ] && PYTHONPATH="$REPO/src" "$REPO/.venv/bin/python" - "$LOG" <<'PY'
import re, sys
from phctx.config import load
allowed = {h.lower() for h in load().allowed_download_hosts}
lines = open(sys.argv[1]).read().splitlines()
refused = {re.sub(r'.*file_fetch host=', '', a) for a, b in zip(lines, lines[1:])
           if 'file_fetch host=' in a and 'file_host_not_allowlisted' in b}
if pending := sorted(refused - allowed):
    print('refused file hosts (add to allowed_download_hosts if they are ChatGPT file storage):', *pending, sep='\n  ')
PY
