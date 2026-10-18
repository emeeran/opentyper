#!/usr/bin/env bash
# OpenTyper backup: SQLite online backup + rotation + restore test.
# Idempotent; supports --dry-run. Run as root or as the opentyper user.
#
# Cron example (daily 03:17):
#   17 3 * * * /opt/opentyper/deploy/backup.sh >> /var/log/opentyper/backup.log 2>&1
set -euo pipefail

DATA_DIR="${OPENTYPER_DATA_DIR:-/opt/opentyper/data}"
BACKUP_DIR="${OPENTYPER_BACKUP_DIR:-/var/backups/opentyper}"
KEEP=7
DB="$DATA_DIR/opentyper.db"

DRY_RUN=0
log() { echo "[backup] $*"; }
die() { echo "[backup] ERROR: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --keep) KEEP="$2"; shift ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done

[[ -f "$DB" ]] || die "database not found at $DB"
command -v sqlite3 >/dev/null 2>&1 || die "sqlite3 CLI required (apt install sqlite3)"

STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$BACKUP_DIR/opentyper-$STAMP.db"

if [[ $DRY_RUN -eq 1 ]]; then
  log "DRY-RUN: sqlite3 '$DB' \".backup '$DEST'\""
  log "DRY-RUN: integrity check + restore test + rotate keep=$KEEP in $BACKUP_DIR"
  exit 0
fi

mkdir -p "$BACKUP_DIR"

# 1. online backup (safe while the app is running; WAL-aware)
log "backing up $DB -> $DEST"
sqlite3 "$DB" ".backup '$DEST'"

# 2. integrity check on the copy
if ! sqlite3 "$DEST" "PRAGMA integrity_check;" | grep -q "^ok$"; then
  die "integrity check failed on $DEST"
fi

# 3. restore test: replay the backup into a scratch db and count objects
TEST_DB="$(mktemp)"
trap 'rm -f "$TEST_DB"' EXIT
sqlite3 "$TEST_DB" ".restore '$DEST'"
OBJECTS="$(sqlite3 "$TEST_DB" "SELECT count(*) FROM sqlite_master WHERE type='table';")"
CHATS="$(sqlite3 "$TEST_DB" "SELECT count(*) FROM chats;" 2>/dev/null || echo 0)"
log "restore test passed: $OBJECTS tables, $CHATS chats readable"

# 4. rotation (opentyper-YYYYmmdd-HHMMSS.db sorts lexically = chronologically)
log "rotating (keeping $KEEP newest)"
find "$BACKUP_DIR" -maxdepth 1 -name 'opentyper-*.db' | sort -r |
  tail -n +"$((KEEP + 1))" | while read -r old; do
    log "removing old backup: $old"
    rm -f "$old"
  done

chmod 600 "$DEST"
log "done: $DEST"
