#!/bin/sh
set -eu

set -- \
  --component ran \
  --action "${AMARI_ACTION:-apply}" \
  --state-file "${AMARI_RAN_STATE_FILE:-/var/lib/katana-amari-adapter/ran-slices.json}" \
  --config-file "${AMARI_RAN_CONFIG:-/root/lteenb-linux-2025-09-19/config/gnb-sa.cfg}" \
  --backup-dir "${AMARI_BACKUP_DIR:-/var/lib/katana-amari-adapter/backups}"

if [ -n "${AMARI_RESTART_CMD:-}" ]; then
  set -- "$@" --restart-cmd "$AMARI_RESTART_CMD"
fi

exec /usr/bin/python3 /opt/katana/amari-adapter/amari_config_manager.py "$@"
