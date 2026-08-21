#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: $0 /path/to/backup.dump" >&2
  exit 2
fi

backup="$1"
test -s "$backup"
check_db="memory_sync_restore_check"

docker compose exec -T postgres dropdb --if-exists -U "$POSTGRES_USER" "$check_db"
docker compose exec -T postgres createdb -U "$POSTGRES_USER" "$check_db"
docker compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$check_db" --clean --if-exists < "$backup"
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$check_db" -v ON_ERROR_STOP=1 -c \
  "SELECT count(*) AS accounts FROM auth_accounts; SELECT count(*) AS versions FROM document_versions;"
docker compose exec -T postgres dropdb -U "$POSTGRES_USER" "$check_db"
