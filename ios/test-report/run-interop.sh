#!/bin/bash
set -u

IOS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$IOS_ROOT/.." && pwd)"
cd "$IOS_ROOT" || exit 1
PYTHON="$REPO_ROOT/.venv/bin/python"
TMP_ROOT="$(mktemp -d "$IOS_ROOT/test-report/.interop.XXXXXX")" || exit 1
LOG_PATH="$IOS_ROOT/test-report/interop.log"
exec > >(tee "$LOG_PATH") 2>&1
SERVER_PID=""
cleanup() {
    if [[ -n "$SERVER_PID" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -rf "$TMP_ROOT"
}
trap cleanup EXIT INT TERM

printf 'Synthetic Swift ↔ Python ingest interoperability probe\n'
printf 'Date: '; date '+%Y-%m-%d %H:%M:%S %Z'
printf 'Scope: disposable synthetic store, loopback TLS server, temporary outbox under ios/test-report\n'
if [[ ! -x "$PYTHON" ]]; then
    printf 'ERROR: expected project virtualenv Python at %s\n' "$PYTHON"
    printf 'server startup exit code: 127\n'
    exit 127
fi

SERVER_CODE=$(cat <<'PY'
import json
import os
from pathlib import Path
from phctx.store import Store
from phctx import ingest

root = Path(os.environ['PHCTX_TEST_ROOT'])
store = Store(root / 'store', 'synthetic')
cert, key, fingerprint = ingest.ensure_cert(root / 'tls', ['127.0.0.1'])
pairing_code = ingest.new_pairing_code(store)
server = ingest.make_server(store, '127.0.0.1', 0, cert, key)
print(json.dumps({'host': '127.0.0.1', 'port': server.server_address[1],
                  'fingerprint': fingerprint, 'pairing_code': pairing_code,
                  'db': str(store.db)}), flush=True)
server.serve_forever()
PY
)
(
    cd "$REPO_ROOT" || exit 1
    export PYTHONPATH=src PHCTX_TEST_ROOT="$TMP_ROOT/server"
    exec "$PYTHON" -c "$SERVER_CODE"
) >"$TMP_ROOT/server.json" 2>"$TMP_ROOT/server.stderr" &
SERVER_PID=$!

server_ready=0
for _ in $(seq 1 100); do
    if [[ -s "$TMP_ROOT/server.json" ]]; then
        server_ready=1
        break
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        break
    fi
    sleep 0.1
done
if [[ "$server_ready" -ne 1 ]]; then
    printf 'ERROR: synthetic ingest server did not start\n'
    cat "$TMP_ROOT/server.stderr"
    printf 'server startup exit code: 1\n'
    exit 1
fi
printf 'server startup exit code: 0\n'

HOST_PORT=$("$PYTHON" -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["host"]+" "+str(d["port"]))' "$TMP_ROOT/server.json")
read -r HOST PORT <<< "$HOST_PORT"
FINGERPRINT=$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["fingerprint"])' "$TMP_ROOT/server.json")
PAIRING_CODE=$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["pairing_code"])' "$TMP_ROOT/server.json")
DB_PATH=$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["db"])' "$TMP_ROOT/server.json")
INSTALLATION_ID="interop-$("$PYTHON" -c 'import uuid; print(uuid.uuid4())')"
OUTBOX_PATH="$TMP_ROOT/outbox"

printf '\nCommand: swift build --package-path %s --product InteropProbe\n' "$IOS_ROOT/HealthSyncCore"
swift build --package-path "$IOS_ROOT/HealthSyncCore" --product InteropProbe
build_code=$?
printf 'InteropProbe build exit code: %s\n' "$build_code"
if [[ "$build_code" -ne 0 ]]; then exit "$build_code"; fi

printf '\nCommand: PHCTX_INTEROP=1 swift run --package-path %s InteropProbe %s %s <pinned-cert> <one-time-code>\n' "$IOS_ROOT/HealthSyncCore" "$HOST" "$PORT"
PHCTX_INTEROP=1 PHCTX_INTEROP_INSTALLATION_ID="$INSTALLATION_ID" PHCTX_INTEROP_OUTBOX="$OUTBOX_PATH" \
    swift run --package-path "$IOS_ROOT/HealthSyncCore" InteropProbe "$HOST" "$PORT" "$FINGERPRINT" "$PAIRING_CODE"
probe_code=$?
printf 'InteropProbe run exit code: %s\n' "$probe_code"
if [[ "$probe_code" -ne 0 ]]; then
    cat "$TMP_ROOT/server.stderr"
    exit "$probe_code"
fi

printf '\nCommand: query synthetic SQLite observations and committed receipt counts\n'
PHCTX_TEST_DB="$DB_PATH" PHCTX_TEST_INSTALLATION_ID="$INSTALLATION_ID" "$PYTHON" - <<'PY'
import json
import os
import sqlite3
import sys

db = os.environ['PHCTX_TEST_DB']
installation = os.environ['PHCTX_TEST_INSTALLATION_ID']
source = f'apple_health:{installation}'
with sqlite3.connect(db) as connection:
    active = connection.execute('SELECT count(*) FROM active_observations WHERE source_id=?', (source,)).fetchone()[0]
    oura = connection.execute("SELECT count(*) FROM active_observations WHERE source_id=? AND (source_name LIKE '%Oura%' OR raw_json LIKE '%Oura%')", (source,)).fetchone()[0]
    receipts = [json.loads(row[0]) for row in connection.execute(
        'SELECT result_json FROM receipts WHERE request_id LIKE ?', (f'hk:{installation}:%',))]
filtered_counts = sorted(item.get('filtered_restricted') for item in receipts)
result = {'active_observations': active, 'oura_observations': oura,
          'batch_receipts': len(receipts), 'server_filtered_restricted_counts': filtered_counts}
print(json.dumps(result, sort_keys=True))
if active != 3 or oura != 0 or len(receipts) != 3 or filtered_counts != [0, 0, 0]:
    print('database assertion failed', file=sys.stderr)
    sys.exit(1)
PY
db_code=$?
printf 'Synthetic database assertion exit code: %s\n' "$db_code"
if [[ "$db_code" -ne 0 ]]; then exit "$db_code"; fi

printf '\nOverall interop exit code: 0\n'
exit 0
