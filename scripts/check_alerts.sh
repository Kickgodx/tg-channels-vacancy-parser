#!/usr/bin/env bash
# Проверка алертинга без запуска стека — в тех же образах, что в docker-compose.yml:
#   promtool check config  — prometheus.yml вместе с правилами из observability/alerts/
#   promtool test rules    — юнит-тесты правил (observability/alerts/tests/*.test.yml)
#   amtool check-config    — observability/alertmanager.yml
# Нужен Docker. Запуск из любого каталога: scripts/check_alerts.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# Git Bash на Windows переписывает пути вида /etc/... в аргументах docker и не понимает
# /c/... в -v: отключаем конвертацию и отдаём docker путь в виде C:\...
export MSYS_NO_PATHCONV=1
obs="$PWD/observability"
if command -v cygpath >/dev/null 2>&1; then
    obs="$(cygpath -w "$obs")"
fi

image_of() {
    # Образ сервиса из docker-compose.yml: первая строка image: после «  <service>:».
    sed -n "/^  $1:\$/,/image:/s/^ *image: *//p" docker-compose.yml | head -n 1
}

prom_image=$(image_of prometheus)
am_image=$(image_of alertmanager)
[ -n "$prom_image" ] && [ -n "$am_image" ] || { echo "не нашёл образы prometheus/alertmanager в docker-compose.yml" >&2; exit 1; }

prom() {
    docker run --rm \
        -v "$obs/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
        -v "$obs/alerts:/etc/prometheus/alerts:ro" \
        --entrypoint /bin/sh "$prom_image" -c "$1"
}

echo "== promtool check config ($prom_image)"
prom "promtool check config /etc/prometheus/prometheus.yml"

echo "== promtool test rules"
prom "cd /etc/prometheus/alerts/tests && promtool test rules *.test.yml"

echo "== amtool check-config ($am_image)"
docker run --rm \
    -v "$obs/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro" \
    --entrypoint amtool "$am_image" check-config /etc/alertmanager/alertmanager.yml

echo "OK: алерты и конфиги корректны"
