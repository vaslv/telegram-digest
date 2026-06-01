#!/bin/sh
set -e

# Wait for PostgreSQL to accept connections before touching the schema.
DB_HOST="${POSTGRES_HOST:-db}"
DB_PORT="${POSTGRES_PORT:-5432}"
DB_USER="${POSTGRES_USER:-tgdigest}"

echo "entrypoint: waiting for postgres at ${DB_HOST}:${DB_PORT}..."
until pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" >/dev/null 2>&1; do
    sleep 1
done

echo "entrypoint: applying migrations"
alembic upgrade head

echo "entrypoint: seeding default prompts (idempotent)"
tgdigest seed-prompts || echo "entrypoint: seed-prompts skipped"

exec "$@"
