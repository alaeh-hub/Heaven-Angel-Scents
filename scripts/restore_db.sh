#!/usr/bin/env bash
# Restores a database dump produced by backup_db.sh. See ../BACKUP.md's
# "Testing a restore" section — the whole point of a backup strategy is
# a restore that's actually been tried, not just a pile of .sql.gz files
# nobody has opened. Always restore into --target-db pointed at a
# throwaway database name when you're just verifying a backup is good;
# never straight into the database an in-use app is currently reading.
#
# Usage:
#   ./scripts/restore_db.sh <dump-file.sql.gz> [--target-db NAME] [--force]
#
#   --target-db NAME   database to (re)create and restore into
#                       (default: MYSQL_DB from .env/environment)
#   --force            skip the confirmation prompt (for scripted use)
set -euo pipefail

cd "$(dirname "$0")/.."

usage() {
  echo "Usage: $0 <dump-file.sql.gz> [--target-db NAME] [--force]" >&2
  exit 1
}

[ $# -ge 1 ] || usage

DUMP_FILE="$1"
shift
FORCE=0
TARGET_DB_OVERRIDE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --target-db) TARGET_DB_OVERRIDE="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done

[ -f "$DUMP_FILE" ] || { echo "No such file: $DUMP_FILE" >&2; exit 1; }

# See backup_db.sh's _env_get comment: .env is written for python-dotenv,
# not for a literal bash `source`, which chokes on its unquoted-spaces
# values (e.g. AI_CHAT_RATE_LIMIT=15 per minute;150 per day).
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
TARGET_DB="${TARGET_DB_OVERRIDE:-$(_env_get MYSQL_DB heaven_and_angel_scents)}"

if [ "$FORCE" -ne 1 ]; then
  read -r -p "This will DROP and recreate '$TARGET_DB' on $MYSQL_HOST:$MYSQL_PORT from $DUMP_FILE. Continue? [y/N] " reply
  case "$reply" in
    [yY]*) ;;
    *) echo "Aborted."; exit 1 ;;
  esac
fi

export MYSQL_PWD="$MYSQL_PASSWORD"

echo "Dropping and recreating '$TARGET_DB'..."
mysql --host="$MYSQL_HOST" --port="$MYSQL_PORT" --user="$MYSQL_USER" \
  -e "DROP DATABASE IF EXISTS \`$TARGET_DB\`; CREATE DATABASE \`$TARGET_DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

echo "Restoring $DUMP_FILE into '$TARGET_DB'..."
gunzip -c "$DUMP_FILE" | mysql --host="$MYSQL_HOST" --port="$MYSQL_PORT" --user="$MYSQL_USER" "$TARGET_DB"

echo
echo "Restore complete into '$TARGET_DB'. Before trusting it:"
echo "  - Point a throwaway MYSQL_DB=$TARGET_DB app instance at it and log in."
echo "  - Spot-check recent rows (e.g. SELECT * FROM sales ORDER BY sold_at DESC LIMIT 5)."
echo "  - Confirm the row counts look right for branches/products/users."
