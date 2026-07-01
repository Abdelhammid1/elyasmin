#!/usr/bin/env bash
# Daily automated backup — NFR requirement.
#
# Cron example (runs every day at 02:00):
#   0 2 * * *  /Users/ibrahim/Desktop/my\ projects/farm/scripts/backup.sh
#
# For PostgreSQL: swap the sqlite3 line for `pg_dump "$DATABASE_URL" > "$OUT"`.

set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DB="$APP_DIR/instance/farm.db"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/instance/backups}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$BACKUP_DIR/farm_${STAMP}.db"

mkdir -p "$BACKUP_DIR"

# .backup produces a consistent snapshot even during writes
sqlite3 "$DB" ".backup '$OUT'"

# Retention: keep 30 days
find "$BACKUP_DIR" -name "farm_*.db" -mtime +30 -delete

echo "[backup] $OUT ($(du -h "$OUT" | cut -f1))"
