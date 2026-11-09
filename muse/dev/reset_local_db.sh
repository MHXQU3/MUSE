#!/usr/bin/env bash
# Rebuild a local test database from the migrations (local Postgres only).
# Usage: dev/reset_local_db.sh [dbname]   (uses PG* env vars for the server)
set -euo pipefail
cd "$(dirname "$0")/.."
DB="${1:-muse_test}"
psql -X -q -d postgres -c "drop database if exists $DB" -c "create database $DB"
psql -X -q -v ON_ERROR_STOP=1 -d "$DB" -f dev/supabase_stub.sql
for f in supabase/migrations/*.sql; do
  psql -X -q -v ON_ERROR_STOP=1 -d "$DB" -f "$f"
done
echo "Database $DB rebuilt."
