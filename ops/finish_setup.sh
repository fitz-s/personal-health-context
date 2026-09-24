#!/bin/bash
# One-shot finisher for the two steps that need you personally:
#   1) paste the tunnel runtime API key (stored in Keychain; never echoed), then the tunnel starts under launchd
#   2) accept the Xcode license (sudo), then the iPhone helper is built for a connected, unlocked iPhone
# Usage: ~/personal-health-context/ops/finish_setup.sh [tunnel|xcode|all]
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TUNNEL_ID="${PHCTX_TUNNEL_ID:-$(cat "$HOME/.config/phctx/tunnel_id" 2>/dev/null)}"
[ -n "$TUNNEL_ID" ] || { echo "set PHCTX_TUNNEL_ID or write it to ~/.config/phctx/tunnel_id" >&2; exit 2; }
what="${1:-all}"

if [ "$what" = tunnel ] || [ "$what" = all ]; then
  if ! /usr/bin/security find-generic-password -s phctx-tunnel-key >/dev/null 2>&1; then
    echo "Create a key at https://platform.openai.com/settings/organization/api-keys (Tunnels: Read + Use)."
    echo "Paste it at the prompt (input hidden):"
    /usr/bin/security add-generic-password -s phctx-tunnel-key -a "$USER" -U -w
  fi
  "$REPO/ops/tunnel.sh" init "$TUNNEL_ID"
  "$REPO/ops/tunnel.sh" install
  echo "Tunnel started. Tell Claude: 'tunnel 好了'."
fi

if [ "$what" = xcode ] || [ "$what" = all ]; then
  sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
  sudo xcodebuild -license accept
  sudo xcodebuild -runFirstLaunch
  echo "Xcode ready. Open Xcode once → Settings → Accounts → add your Apple ID (free account is fine)."
  echo "Connect and unlock your iPhone, enable Developer Mode on it, then tell Claude: 'xcode 好了'."
fi
