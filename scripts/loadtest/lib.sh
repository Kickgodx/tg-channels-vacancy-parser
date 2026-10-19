#!/usr/bin/env bash
# Общее для скриптов нагрузочного теста: корень проекта, сеть compose, пути для Docker.

LT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$LT_DIR/../.." && pwd)"
# Сеть compose: <проект>_default, проект по умолчанию — имя каталога.
LT_NETWORK="${LT_NETWORK:-${COMPOSE_PROJECT_NAME:-$(basename "$ROOT")}_default}"
LT_DB="${LT_DB:-tgjobs_lt}"
# Git Bash на Windows переписывает пути в аргументах docker; отключаем, пути для -v даём сами.
export MSYS_NO_PATHCONV=1

host_path() {
  # Путь каталога хоста для docker -v (на Windows — C:\..., иначе как есть).
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else echo "$1"; fi
}

env_value() {
  # Значение переменной из .env без source (там могут быть символы, которые shell раскроет).
  { grep -E "^$1=" "$ROOT/.env" 2>/dev/null || true; } | tail -1 | cut -d= -f2-
}

pg() {
  # psql/pg_* в контейнере postgres compose.
  docker compose -f "$(host_path "$ROOT/docker-compose.yml")" exec -T postgres "$@"
}
