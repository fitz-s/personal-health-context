#!/bin/bash
# Run the official Secure MCP Tunnel client as the single owner of the phctx stdio MCP server.
#   One-time:  security add-generic-password -s phctx-tunnel-key -a phctx -w     (paste runtime API key; prompts)
#              ops/tunnel.sh init tunnel_XXXXXXXX
#   Run:       ops/tunnel.sh run        (or install the launchd agent: ops/tunnel.sh install)
#   Read-only second connection for any conversation that also uses Oura (write tools absent server-side):
#              ops/tunnel.sh init tunnel_YYYYYYYY readonly ; ops/tunnel.sh install readonly
# The key is read from Keychain into this process only; it never lands in a file, plist or argv.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TC="$REPO/tools/tunnel-client/tunnel-client"
MODE="full"
for a in "$@"; do [ "$a" = readonly ] && MODE=readonly; done
PROFILE=phctx-local; [ "$MODE" = readonly ] && PROFILE=phctx-readonly
CFG="${PHCTX_CONFIG:-$HOME/.config/phctx/config.toml}"
LOG_DIR="$HOME/Library/Logs/PersonalHealthContext"
# Runtime key source, in order: Keychain item phctx-tunnel-key, else the existing WebCodex tunnel config (same
# Platform org, Tunnels Read+Use). Read into this process only; never written anywhere else.
WEBCODEX_CFG="$HOME/Library/Application Support/dev.webcodex.desktop/secrets/tunnel-config.json"
key() {
  /usr/bin/security find-generic-password -s phctx-tunnel-key -w 2>/dev/null && return
  [ -r "$WEBCODEX_CFG" ] && /usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["api_key"])' "$WEBCODEX_CFG" && return
  echo "no runtime key: Keychain phctx-tunnel-key or WebCodex tunnel config" >&2; exit 3
}
MCP_CMD="env PYTHONPATH=$REPO/src PHCTX_CONFIG=$CFG $REPO/.venv/bin/python -m phctx mcp --profile $MODE --log $LOG_DIR/mcp-$MODE.log"
case "${1:-}" in
  init)
    TID="${2:?usage: $0 init tunnel_...}"
    "$TC" init --sample sample_mcp_stdio_local --profile "$PROFILE" --tunnel-id "$TID" --mcp-command "$MCP_CMD" \
      --health-listen-addr 127.0.0.1:0 --force
    CONTROL_PLANE_API_KEY="$(key)" "$TC" doctor --profile "$PROFILE" --explain ;;
  doctor) CONTROL_PLANE_API_KEY="$(key)" exec "$TC" doctor --profile "$PROFILE" --explain ;;
  run) CONTROL_PLANE_API_KEY="$(key)" exec "$TC" run --profile "$PROFILE" ;;
  install)
    LABEL=com.personalhealthcontext.tunnel; [ "$MODE" = readonly ] && LABEL=com.personalhealthcontext.tunnel-readonly
    P="$HOME/Library/LaunchAgents/$LABEL.plist"
    mkdir -p "$LOG_DIR"
    cat > "$P" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$REPO/ops/tunnel.sh</string><string>run</string><string>$MODE</string></array>
  <key>KeepAlive</key><true/><key>RunAtLoad</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOG_DIR/$LABEL.out.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/$LABEL.err.log</string>
</dict></plist>
PL
    plutil -lint "$P" >/dev/null
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$P" && echo "loaded $LABEL" ;;
  *) echo "usage: $0 init TUNNEL_ID [readonly] | doctor [readonly] | run [readonly] | install [readonly]" >&2; exit 2 ;;
esac
