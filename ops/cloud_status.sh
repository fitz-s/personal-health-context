#!/bin/bash
# Daily cloud analysis: digests saved in the last 14 days, connector calls per day, and today's bootstrap size.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$HOME/Library/Logs/PersonalHealthContext/mcp-full.log"
PYTHONPATH="$REPO/src" "$REPO/.venv/bin/python" - "$LOG" <<'PY'
import collections, json, sys
from phctx.config import load
from phctx.store import Store
s = Store(load().root, 'production')
b = s.bootstrap()
print(f"bootstrap: {len(json.dumps(b, ensure_ascii=False))} chars; ledger {len(b['analysis_ledger'])} analyses")
print('digests (last 14 days):')
with s.connect() as c:
    for at, text in c.execute("SELECT occurred_at, json_extract(payload_json, '$.summary') FROM active_records "
                              "WHERE kind='analysis' AND json_extract(payload_json, '$.type')='digest' "
                              "AND occurred_at > datetime('now', '-14 days') ORDER BY occurred_at DESC"):
        print(f'  {at[:16]}  {text}')
calls = collections.Counter()
try:
    for line in open(sys.argv[1]):
        if ' tool=' in line:
            calls[line[:10]] += 1
except FileNotFoundError:
    pass
print('connector calls per day:', dict(sorted(calls.items())[-7:]))
PY
