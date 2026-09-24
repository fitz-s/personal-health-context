#!/bin/bash
# Stop and remove the launchd agents. Keeps ALL data, config, backups and Keychain items.
# Full data deletion is a separate, explicit manual step (see delivery/USER_HANDOVER.md).
set -euo pipefail
LA="$HOME/Library/LaunchAgents"
for label in com.personalhealthcontext.worker com.personalhealthcontext.ingest com.personalhealthcontext.backup com.personalhealthcontext.oura com.personalhealthcontext.whoop com.personalhealthcontext.tunnel com.personalhealthcontext.tunnel-readonly; do
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null && echo "stopped $label" || true
  rm -f "$LA/$label.plist"
done
echo "Services removed. Data kept in: $HOME/Library/Application Support/PersonalHealthContext"
