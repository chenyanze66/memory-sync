#!/bin/sh
set -eu

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${BACKUP_DIR:=/var/backups/memory-sync}"
: "${BACKUP_RETENTION_DAYS:=14}"

umask 077
mkdir -p "$BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$BACKUP_DIR/${POSTGRES_DB}_${stamp}.dump"

docker compose exec -T postgres pg_dump \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --format custom \
  --compress 9 > "$target"

test -s "$target"
find "$BACKUP_DIR" -type f -name "${POSTGRES_DB}_*.dump" -mtime "+$BACKUP_RETENTION_DAYS" -delete
printf 'backup=%s bytes=%s\n' "$target" "$(wc -c < "$target")"
