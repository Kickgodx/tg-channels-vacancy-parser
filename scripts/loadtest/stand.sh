#!/usr/bin/env bash
# Стенд нагрузочного теста: отдельный контейнер API (lt-api) на копии базы из clone_db.sh.
#
#   scripts/loadtest/stand.sh [API_WORKERS]   # поднять (по умолчанию 4 процесса)
#   scripts/loadtest/stand.sh stop            # остановить
#
# Образ — тот же, что у backend compose (LT_IMAGE, по умолчанию tg_jobs_parser-backend;
# после правок кода — docker compose build backend). Стенд в сети compose, порт на хост
# не публикуется: k6 ходит изнутри сети (run.sh). Redis — свой номер базы (15), задачи —
# rq, но воркеров на этой базе Redis нет: LLM-задачи стенд не выполняет.
#
# Переменные: LT_IMAGE, LT_DB, LT_OTEL=1 (трассы в Alloy с service.name=loadtest-backend),
# LT_APP_ENV (по умолчанию из .env), LT_CPUS (ограничить CPU контейнера, например 2).
set -euo pipefail
source "$(dirname "$0")/lib.sh"

NAME=lt-api
docker rm -f "$NAME" >/dev/null 2>&1 || true
if [ "${1:-}" = "stop" ]; then
  echo "стенд остановлен"
  exit 0
fi

WORKERS="${1:-4}"
PG_PASSWORD="$(env_value POSTGRES_PASSWORD)"
REDIS_PASSWORD="$(env_value REDIS_PASSWORD)"
APP_ENV_VALUE="${LT_APP_ENV:-$(env_value APP_ENV)}"
OTEL=false
[ "${LT_OTEL:-0}" = "1" ] && OTEL=true

ENV_FILE="$(mktemp)"
trap 'rm -f "$ENV_FILE"' EXIT
# Через --env-file: пароли не попадают в argv (ps, история shell).
cat >"$ENV_FILE" <<EOF
DATABASE_URL=postgresql+psycopg://tgjobs:${PG_PASSWORD}@postgres:5432/${LT_DB}
REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/15
CONFIG_PATH=/app/config.yaml
LOG_DIR=
APP_ENV=${APP_ENV_VALUE:-dev}
API_HOST=0.0.0.0
API_PUBLIC_ADDR=127.0.0.1
API_WORKERS=${WORKERS}
API_METRICS_PORT=9100
JWT_SECRET=loadtest-$(date +%s)-$RANDOM-$RANDOM-$RANDOM-secret
PARSE_JOB_BACKEND=rq
LLM_JOB_BACKEND=rq
HH_SYNC_JOB_BACKEND=rq
AUTH_RATE_LIMIT_PER_MINUTE=0
AUTH_MAX_FAILED_LOGINS=0
JWT_TTL_MINUTES=720
OTEL_ENABLED=${OTEL}
OTEL_EXPORTER_OTLP_ENDPOINT=http://alloy:4318
OTEL_SERVICE_NAME=loadtest-backend
EOF

LIMITS=()
[ -n "${LT_CPUS:-}" ] && LIMITS+=(--cpus "$LT_CPUS")

docker run -d --name "$NAME" --network "$LT_NETWORK" --env-file "$(host_path "$ENV_FILE")" \
  -v "$(host_path "$ROOT/config.yaml"):/app/config.yaml:ro" \
  -v "$(host_path "$ROOT/llm_models.yaml"):/app/llm_models.yaml:ro" \
  "${LIMITS[@]}" "${LT_IMAGE:-tg_jobs_parser-backend}" >/dev/null

for _ in $(seq 1 90); do
  status="$(docker exec "$NAME" python -c "import json,urllib.request;print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health',timeout=3))['status'])" 2>/dev/null || true)"
  if [ "$status" = "ok" ]; then
    echo "стенд $NAME готов: API_WORKERS=$WORKERS, база $LT_DB, сеть $LT_NETWORK"
    exit 0
  fi
  sleep 1
done
echo "стенд не поднялся:" >&2
docker logs --tail 40 "$NAME" >&2
exit 1
