#!/usr/bin/env bash
# Прогон k6 в сети compose против стенда (stand.sh).
#
#   scripts/loadtest/run.sh EP VUS [DUR]      # например: run.sh mix 32 30s
#
# Итог — JSON-строка в stdout и HTML-отчёт k6 web dashboard в scripts/loadtest/out/.
# LIVE=1     — живой дашборд на http://localhost:5665 во время прогона;
# PROM_RW=1  — метрики k6 в Prometheus compose (remote write), дашборд Grafana «k6 load test»;
# BASE       — другой адрес API (по умолчанию http://lt-api:8000/api/v1).
set -euo pipefail
source "$(dirname "$0")/lib.sh"

EP="${1:?EP: mix, list, list_q, me, detail, metrics_miss, … (см. k6/catalog.js)}"
VUS="${2:-16}"
DUR="${3:-20s}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LT_DIR/out"

ARGS=(
  -e EP="$EP" -e VUS="$VUS" -e DUR="$DUR"
  -e K6_WEB_DASHBOARD=true
  -e K6_WEB_DASHBOARD_EXPORT="/out/report-$EP-$VUS-$STAMP.html"
  -e K6_WEB_DASHBOARD_HOST=0.0.0.0
  # Снимок раз в 2 с: при умолчании (10 с) отчёт короткого прогона k6 не пишет.
  -e K6_WEB_DASHBOARD_PERIOD=2s
)
[ -n "${BASE:-}" ] && ARGS+=(-e BASE="$BASE")
[ "${LIVE:-0}" = "1" ] && ARGS+=(-p 127.0.0.1:5665:5665)
OUT=()
if [ "${PROM_RW:-0}" = "1" ]; then
  ARGS+=(
    -e K6_PROMETHEUS_RW_SERVER_URL=http://prometheus:9090/api/v1/write
    -e K6_PROMETHEUS_RW_TREND_STATS="p(50),p(95),p(99),avg,max"
  )
  OUT+=(-o experimental-prometheus-rw --tag "testid=$EP-$VUS-$STAMP")
fi

docker run --rm --network "$LT_NETWORK" "${ARGS[@]}" \
  -v "$(host_path "$LT_DIR/k6"):/scripts:ro" \
  -v "$(host_path "$LT_DIR/out"):/out" \
  "${K6_IMAGE:-grafana/k6:0.57.0}" run --quiet --no-color "${OUT[@]}" /scripts/catalog.js
REPORT="scripts/loadtest/out/report-$EP-$VUS-$STAMP.html"
if [ -f "$ROOT/$REPORT" ]; then echo "отчёт: $REPORT" >&2; else echo "отчёт не записан (слишком короткий прогон?)" >&2; fi
