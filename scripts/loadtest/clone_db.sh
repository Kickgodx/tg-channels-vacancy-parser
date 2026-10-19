#!/usr/bin/env bash
# Копия рабочей базы для нагрузочного теста: tgjobs → $LT_DB (по умолчанию tgjobs_lt).
#
#   scripts/loadtest/clone_db.sh [МНОЖИТЕЛЬ]   # МНОЖИТЕЛЬ — во сколько раз размножить вакансии (1 — без)
#   scripts/loadtest/clone_db.sh drop          # удалить копию
#
# Рабочую базу скрипт только читает (pg_dump). Копии вакансий получают новые id и
# source_post_key с суффиксом -xN, даты сдвинуты на случайные минуты.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if [ "$LT_DB" = "tgjobs" ]; then
  echo "LT_DB=tgjobs — это рабочая база, нагрузочный тест её не трогает" >&2
  exit 1
fi

pg dropdb -U tgjobs --if-exists --force "$LT_DB"
if [ "${1:-1}" = "drop" ]; then
  echo "копия $LT_DB удалена"
  exit 0
fi

MULT="${1:-1}"
pg createdb -U tgjobs "$LT_DB"
pg sh -c "pg_dump -U tgjobs tgjobs | psql -q -U tgjobs -d $LT_DB >/dev/null"

if [ "$MULT" -gt 1 ]; then
  pg psql -q -U tgjobs -d "$LT_DB" -v ON_ERROR_STOP=1 -v mult="$MULT" <<'SQL'
-- psql не подставляет :переменные внутри $$...$$ — передаём через настройку сессии.
SELECT set_config('lt.mult', :'mult', false);
DO $$
DECLARE cols text; n int; mult int := current_setting('lt.mult')::int;
BEGIN
  SELECT string_agg(quote_ident(column_name), ',') INTO cols
    FROM information_schema.columns
   WHERE table_name = 'vacancies' AND is_generated = 'NEVER'
     AND column_name NOT IN ('id', 'post_date', 'source_post_key');
  FOR n IN 1..mult - 1 LOOP
    EXECUTE format(
      'INSERT INTO vacancies (id, source_post_key, post_date, %s)
       SELECT md5(id || %L) || md5(%L || id), source_post_key || %L,
              post_date - make_interval(mins => (random() * 600)::int), %s
         FROM vacancies WHERE source_post_key NOT LIKE %L',
      cols, n, n, '-x' || n, cols, '%-x%');
  END LOOP;
END $$;
ANALYZE vacancies;
SQL
fi

pg psql -U tgjobs -d "$LT_DB" -tAc \
  "SELECT 'копия $LT_DB: ' || count(*) || ' вакансий, ' || pg_size_pretty(pg_total_relation_size('vacancies')) FROM vacancies"
