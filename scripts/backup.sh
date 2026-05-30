#!/bin/bash
# LCloud backup script.
#
# Backs up:
#   - data/lcloud.db (SQLite, via sqlite3 .backup so it's consistent
#     even while LCloud is running)
#   - data/keys/ (admin keypair, jwt secret, admin.tgid)
#
# Does NOT back up:
#   - data/session.lcloud.session (Telethon — re-create on phone+code login)
#   - data/tmp/ (preview cache)
#   - logs/, .venv/, web/node_modules/, web/dist/
#
# Output: /root/lcloud-backups/lcloud-YYYY-MM-DD-HHMMSS.tar.gz
# Retention: keeps last 14 backups, deletes older.
#
# Usage:
#   /root/LCloud/scripts/backup.sh         # one-shot
#   systemctl start lcloud-backup.timer    # via systemd
#
# Restore:
#   tar -xzf lcloud-YYYY-MM-DD-HHMMSS.tar.gz -C /
#   systemctl restart lcloud

set -euo pipefail

PROJECT_DIR="${LCLOUD_PROJECT_DIR:-/root/LCloud}"
BACKUP_DIR="${LCLOUD_BACKUP_DIR:-/root/lcloud-backups}"
RETENTION="${LCLOUD_BACKUP_RETENTION:-14}"

if [[ ! -d "$PROJECT_DIR/data" ]]; then
    echo "[backup] $PROJECT_DIR/data not found — abort"
    exit 1
fi

mkdir -p "$BACKUP_DIR"

TS=$(date +%Y-%m-%d-%H%M%S)
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

# 1. SQLite consistent dump
echo "[backup $TS] dumping sqlite…"
sqlite3 "$PROJECT_DIR/data/lcloud.db" \
    ".backup '$WORK_DIR/lcloud.db'"

# 2. Keys directory (admin.key, admin.pub, jwt.secret, admin.tgid)
echo "[backup $TS] copying keys…"
cp -r "$PROJECT_DIR/data/keys" "$WORK_DIR/keys"
# Ensure mode 600 on private keys is preserved
find "$WORK_DIR/keys" -type f -name '*.key' -exec chmod 600 {} \;
find "$WORK_DIR/keys" -type f -name 'jwt.secret' -exec chmod 600 {} \;
find "$WORK_DIR/keys" -type f -name 'admin.tgid' -exec chmod 600 {} \;

# 3. Tarball
ARCHIVE="$BACKUP_DIR/lcloud-$TS.tar.gz"
tar -czf "$ARCHIVE" -C "$WORK_DIR" lcloud.db keys
chmod 600 "$ARCHIVE"

SIZE=$(du -h "$ARCHIVE" | awk '{print $1}')
echo "[backup $TS] wrote $ARCHIVE ($SIZE)"

# 4. Retention: keep newest $RETENTION archives, delete the rest
echo "[backup $TS] applying retention (keep $RETENTION newest)…"
ls -1t "$BACKUP_DIR"/lcloud-*.tar.gz 2>/dev/null \
    | tail -n +"$((RETENTION + 1))" \
    | while read -r old; do
        echo "[backup $TS]   deleting $old"
        rm -f "$old"
      done

echo "[backup $TS] done"
