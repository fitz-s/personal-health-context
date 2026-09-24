#!/bin/bash
# Install Personal Health Context for the current user.
#   ops/install.sh [--profile production|synthetic] [--with-ingest] [--with-worker] [--no-load]
# Creates config/data dirs (0700), writes config.toml if absent, installs launchd agents with absolute paths,
# and loads them. Never deletes data. No secrets in plists.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE=production; WITH_INGEST=0; WITH_WORKER=1; LOAD=1
while [ $# -gt 0 ]; do case "$1" in
  --profile) PROFILE="$2"; shift 2;;
  --with-ingest) WITH_INGEST=1; shift;;
  --no-worker) WITH_WORKER=0; shift;;
  --no-load) LOAD=0; shift;;
  *) echo "unknown option $1" >&2; exit 2;; esac; done
CONF_DIR="${PHCTX_CONFIG_DIR:-$HOME/.config/phctx}"
DATA_BASE="$HOME/Library/Application Support/PersonalHealthContext"
LOG_DIR="$HOME/Library/Logs/PersonalHealthContext"
LA="$HOME/Library/LaunchAgents"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "missing $PY — run: cd '$REPO' && uv sync" >&2; exit 1; }
mkdir -p "$CONF_DIR" "$DATA_BASE" "$LOG_DIR" "$LA"; chmod 700 "$CONF_DIR" "$DATA_BASE" "$LOG_DIR"
CFG="$CONF_DIR/config.toml"
if [ ! -f "$CFG" ]; then
  umask 077
  cat > "$CFG" <<TOML
[app]
profile = "$PROFILE"
root = "$DATA_BASE/$PROFILE"
timezone = "America/Chicago"
tool_profile = "full"

[files]
max_interactive_bytes = 26214400
# Exact hosts observed in the live ChatGPT file probe. Empty = every download is refused.
allowed_download_hosts = []
# Set true only if `phctx doctor` reports dns_fake_ip_detected (TUN/fake-IP DNS proxy). Private ranges stay blocked.
trust_fake_ip_dns = false

[model]
enabled = false
backend = "none"          # codex_cli | none
model_id = ""
daily_call_cap = 12

[worker]
mode = "shadow"
change_check_seconds = 900
minimum_semantic_interval_seconds = 21600

[attention]
macos_notification_enabled = false

[backup]
local_dir = "$DATA_BASE/backups"
# cloud_dir = "$HOME/Library/Mobile Documents/com~apple~CloudDocs/PersonalHealthContextBackups"
keychain_service = "phctx-backup"

[ingest]
host = "0.0.0.0"
port = 47821
TOML
  echo "wrote $CFG"
else
  echo "kept existing $CFG"
fi
export PYTHONPATH="$REPO/src"
"$PY" -m phctx init >/dev/null
plist() { # label, args..., interval(optional via env INTERVAL), keepalive via env KEEP
  local label="$1"; shift
  local file="$LA/$label.plist"
  {
    echo '<?xml version="1.0" encoding="UTF-8"?>'
    echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    echo '<plist version="1.0"><dict>'
    echo "  <key>Label</key><string>$label</string>"
    echo '  <key>ProgramArguments</key><array>'
    for a in "$PY" -m phctx "$@"; do echo "    <string>$a</string>"; done
    echo '  </array>'
    echo "  <key>EnvironmentVariables</key><dict><key>PYTHONPATH</key><string>$REPO/src</string><key>PHCTX_CONFIG</key><string>$CFG</string></dict>"
    echo "  <key>WorkingDirectory</key><string>$REPO</string>"
    if [ -n "${INTERVAL:-}" ]; then echo "  <key>StartInterval</key><integer>$INTERVAL</integer><key>RunAtLoad</key><true/>"; fi
    if [ -n "${KEEP:-}" ]; then echo '  <key>KeepAlive</key><true/><key>RunAtLoad</key><true/>'; fi
    echo '  <key>ProcessType</key><string>Background</string>'
    echo "  <key>StandardOutPath</key><string>$LOG_DIR/$label.out.log</string>"
    echo "  <key>StandardErrorPath</key><string>$LOG_DIR/$label.err.log</string>"
    echo '</dict></plist>'
  } > "$file"
  plutil -lint "$file" >/dev/null
  echo "$file"
}
FILES=()
if [ "$WITH_WORKER" = 1 ]; then FILES+=("$(INTERVAL=900 plist com.personalhealthcontext.worker worker --once)"); fi
if [ "$WITH_INGEST" = 1 ]; then FILES+=("$(KEEP=1 plist com.personalhealthcontext.ingest ingest-server)"); fi
FILES+=("$(INTERVAL=86400 plist com.personalhealthcontext.backup backup)")
if [ "$LOAD" = 1 ]; then
  for f in "${FILES[@]}"; do
    label="$(basename "$f" .plist)"
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$f"
    echo "loaded $label"
  done
fi
cat <<MSG
Installed. MCP stdio command for the tunnel / client:
  $PY -m phctx mcp --log "$LOG_DIR/mcp.log"    (env PYTHONPATH=$REPO/src PHCTX_CONFIG=$CFG)
Status: $REPO/ops/status.sh
MSG
