#!/bin/sh
set -eu

set -- \
  --component core \
  --action "${AMARI_ACTION:-apply}" \
  --state-file "${AMARI_CORE_STATE_FILE:-/var/lib/katana-amari-adapter/core-slices.json}" \
  --config-file "${AMARI_CORE_CONFIG:-/root/ltemme-linux-2025-09-19/config/mme-ims.cfg}" \
  --ue-db-file "${AMARI_UE_DB_CONFIG:-/root/ltemme-linux-2025-09-19/config/ue_db-ims.cfg}" \
  --backup-dir "${AMARI_BACKUP_DIR:-/var/lib/katana-amari-adapter/backups}"

if [ -n "${AMARI_RESTART_CMD:-}" ]; then
  set -- "$@" --restart-cmd "$AMARI_RESTART_CMD"
fi

exec /usr/bin/python3 /opt/katana/amari-adapter/amari_config_manager.py "$@"
