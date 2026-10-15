#!/usr/bin/env bash
# Second database for local pytest (see TEST_DATABASE_URL in .env.example).
# Runs only on first container initialisation (empty data volume). A .sh (not .sql) script is
# required here: it's the only docker-entrypoint-initdb.d form that gets the container's own env.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
	CREATE DATABASE "${POSTGRES_DB}_test";
EOSQL
