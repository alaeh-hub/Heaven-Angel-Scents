#!/usr/bin/env bash
# Nightly-cron-friendly backup of the MySQL database and the uploaded
# product photos under static/uploads/ (the only other persistent state
# this app owns that isn't in the database — see schema.sql's comment
# on products.image_path). See ../BACKUP.md for the full strategy this
# script is one half of: schedule, retention, copying off this server,
# and how to actually test a restore.
#
# Usage:
#   ./scripts/backup_db.sh
#
# Reads DB credentials from .env (same as the app itself), or from
# already-exported MYSQL_* env vars if there's no .env (e.g. a
# container/systemd deployment that injects them directly).
#
# Env vars this script itself reads (all optional):
#   BACKUP_DIR             where dumps/archives are written (default ./backups)
#   BACKUP_RETENTION_DAYS  delete local backups older than this (default 14)
set -euo pipefail

cd "$(dirname "$0")/.."

# Reads a single KEY=value line out of .env without sourcing the whole
# file as shell script -- .env is written for python-dotenv's parser
# (e.g. AI_CHAT_RATE_LIMIT=15 per minute;150 per day, unquoted, straight
# from .env.example), and a literal `source .env` chokes on any value
# with unquoted spaces. An already-exported env var always wins over
# whatever .env has, matching python-dotenv's own default precedence.
_env_get() {
  local key="$1" default="$2" val
  if [ -n "${!key:-}" ]; then
    printf '%s' "${!key}"
    return
  fi
  if [ -f .env ]; then
    val="$(grep -E "^${key}=" .env | tail -n1 | cut -d'=' -f2- | tr -d '\r')"
    if [ -n "$val" ]; then
      printf '%s' "$val"
      return
    fi
  fi
  printf '%s' "$default"
}

MYSQL_HOST="$(_env_get MYSQL_HOST localhost)"
MYSQL_PORT="$(_env_get MYSQL_PORT 3306)"
MYSQL_USER="$(_env_get MYSQL_USER root)"
MYSQL_PASSWORD="$(_env_get MYSQL_PASSWORD "")"
MYSQL_DB="$(_env_get MYSQL_DB heaven_and_angel_scents)"

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"

DB_DUMP="$BACKUP_DIR/${MYSQL_DB}_${TIMESTAMP}.sql.gz"
UPLOADS_ARCHIVE="$BACKUP_DIR/uploads_${TIMESTAMP}.tar.gz"

# MYSQL_PWD (rather than -p"$MYSQL_PASSWORD") so the password never
# shows up in `ps`/process listings on a shared host.
export MYSQL_PWD="$MYSQL_PASSWORD"

echo "Backing up database '$MYSQL_DB' -> $DB_DUMP"
mysqldump \
  --host="$MYSQL_HOST" --port="$MYSQL_PORT" --user="$MYSQL_USER" \
  --single-transaction --routines --triggers --events \
  "$MYSQL_DB" | gzip > "$DB_DUMP"

if [ -d static/uploads ]; then
  echo "Backing up uploaded product images -> $UPLOADS_ARCHIVE"
  tar -czf "$UPLOADS_ARCHIVE" static/uploads
fi

echo "Removing local backups older than $RETENTION_DAYS days from $BACKUP_DIR"
find "$BACKUP_DIR" -type f \( -name "*.sql.gz" -o -name "*.tar.gz" \) -mtime "+$RETENTION_DAYS" -delete

echo
echo "Done. Latest backup: $DB_DUMP"
echo "Reminder: $BACKUP_DIR only protects you if this server's disk fails to survive —"
echo "copy new backups off this machine too (see BACKUP.md's 'Off-site copy' section)."
