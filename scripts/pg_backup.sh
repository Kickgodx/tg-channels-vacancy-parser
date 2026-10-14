#!/bin/sh
# Плановый бэкап Postgres с проверкой восстановления — сервис pg-backup в docker-compose.yml.
#
#   pg_backup.sh             цикл: дамп раз в BACKUP_INTERVAL_HOURS (entrypoint сервиса)
#   pg_backup.sh once        внеплановый дамп сейчас
#   pg_backup.sh check FILE  только проверить восстановление существующего дампа
#
# Дамп — pg_dump -Fc всей базы. Проверка: восстановление во временный кластер внутри
# контейнера (боевой сервер не нагружается), сверка набора таблиц и alembic_version с
# источником; таблица, непустая в источнике и пустая после восстановления, — провал.
# Число строк точно не сравнивается: счётчики источника снимаются после дампа, а база
# тем временем пишется. Проверенный дамп получает итоговое имя и пару <имя>.counts;
# не прошедший — суффикс .unverified (удаляется после следующего удачного бэкапа).
# Ротация: хранятся последние BACKUP_KEEP проверенных дампов.
#
# Подключение к источнику — стандартные PGHOST / PGUSER / PGPASSWORD / PGDATABASE.

BACKUP_DIR=${BACKUP_DIR:-/backups}
INTERVAL_HOURS=${BACKUP_INTERVAL_HOURS:-24}
RETRY_MINUTES=${BACKUP_RETRY_MINUTES:-60}
KEEP=${BACKUP_KEEP:-7}
DB=${PGDATABASE:?PGDATABASE не задан}
STAMP="$BACKUP_DIR/.last_verified"
CHECK_DIR=/tmp/restore-check
SRC_COUNTS=/tmp/source.counts
LOCK=/tmp/pg-backup.lock

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) pg-backup: $*"; }

# Таблицы схемы public и число строк в них: «таблица<TAB>count», по алфавиту.
table_counts() {
    psql -X -q -At -v ON_ERROR_STOP=1 -F "$(printf '\t')" "$@" <<'SQL'
SELECT format('SELECT %L, count(*) FROM public.%I', tablename, tablename)
FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename
\gexec
SQL
}

alembic_version() {
    psql -X -q -At -v ON_ERROR_STOP=1 "$@" -c "SELECT string_agg(version_num, ',' ORDER BY version_num) FROM alembic_version"
}

stop_check_cluster() {
    if [ -f "$CHECK_DIR/data/postmaster.pid" ]; then
        su-exec postgres pg_ctl -D "$CHECK_DIR/data" -m immediate -w stop >/dev/null 2>&1
    fi
    rm -rf "$CHECK_DIR"
}

# verify DUMP SOURCE_COUNTS_FILE SOURCE_ALEMBIC — восстановить и сверить.
verify() {
    dump=$1 src_counts=$2 src_alembic=$3
    stop_check_cluster
    mkdir -p "$CHECK_DIR" && chown postgres:postgres "$CHECK_DIR" || return 1
    su-exec postgres initdb -D "$CHECK_DIR/data" -U check --auth=trust >"$CHECK_DIR/initdb.log" 2>&1 || {
        log "initdb временного кластера не удался:"; cat "$CHECK_DIR/initdb.log"; return 1; }
    su-exec postgres pg_ctl -D "$CHECK_DIR/data" -w -l "$CHECK_DIR/server.log" \
        -o "-c listen_addresses='' -c unix_socket_directories=$CHECK_DIR -c fsync=off" start >/dev/null || {
        log "временный кластер не стартовал:"; cat "$CHECK_DIR/server.log"; return 1; }

    conn="-h $CHECK_DIR -U check"
    createdb $conn restored || return 1
    if ! pg_restore $conn -d restored --no-owner --no-acl --exit-on-error "$dump"; then
        log "pg_restore $(basename "$dump") завершился с ошибкой"
        return 1
    fi
    table_counts $conn -d restored >"$CHECK_DIR/restored.counts" || return 1

    rc=0
    if [ -n "$src_alembic" ]; then
        got=$(alembic_version $conn -d restored 2>/dev/null)
        if [ "$got" != "$src_alembic" ]; then
            log "alembic_version: в источнике $src_alembic, после восстановления ${got:-<нет>}"
            rc=1
        fi
    fi
    if [ -n "$src_counts" ]; then
        cut -f1 "$src_counts" >"$CHECK_DIR/src.tables"
        cut -f1 "$CHECK_DIR/restored.counts" >"$CHECK_DIR/restored.tables"
        if ! cmp -s "$CHECK_DIR/src.tables" "$CHECK_DIR/restored.tables"; then
            log "набор таблиц не совпадает (- источник, + восстановлено):"
            diff "$CHECK_DIR/src.tables" "$CHECK_DIR/restored.tables" | grep '^[+-][^+-]'
            rc=1
        fi
        # Таблица с данными в источнике и пустая после восстановления — дамп неполный.
        empty=$(awk -F '\t' 'NR == FNR { src[$1] = $2; next } src[$1] > 0 && $2 == 0 { print $1 }' \
            "$src_counts" "$CHECK_DIR/restored.counts")
        if [ -n "$empty" ]; then
            log "пусты после восстановления, хотя в источнике есть строки: $(echo $empty)"
            rc=1
        fi
    fi
    cp "$CHECK_DIR/restored.counts" "$CHECK_DIR/result.counts" 2>/dev/null
    return $rc
}

prune() {
    # Только проверенные дампы: имя без суффикса, по возрастанию времени в имени.
    ls -1 "$BACKUP_DIR"/"$DB"_*.dump 2>/dev/null | sort | head -n "-$KEEP" | while read -r old; do
        rm -f "$old" "${old%.dump}.counts" && log "ротация: удалён $(basename "$old")"
    done
    rm -f "$BACKUP_DIR"/*.dump.unverified
}

backup_once() {
    mkdir -p "$BACKUP_DIR" || return 1
    rm -f "$BACKUP_DIR"/*.partial
    name="${DB}_$(date -u +%Y%m%d_%H%M%S)"
    part="$BACKUP_DIR/$name.dump.partial"
    started=$(date +%s)

    log "дамп $DB@$PGHOST → $name.dump"
    if ! pg_dump -Fc -f "$part"; then
        log "pg_dump не удался"
        rm -f "$part"
        return 1
    fi
    table_counts >"$SRC_COUNTS" || { log "не удалось снять счётчики источника"; rm -f "$part"; return 1; }
    src_alembic=$(alembic_version 2>/dev/null)
    size=$(du -h "$part" | cut -f1)

    if verify "$part" "$SRC_COUNTS" "$src_alembic"; then
        mv "$part" "$BACKUP_DIR/$name.dump"
        mv "$CHECK_DIR/result.counts" "$BACKUP_DIR/$name.counts"
        date +%s >"$STAMP"
        tables=$(wc -l <"$BACKUP_DIR/$name.counts")
        rows=$(awk -F '\t' '{ s += $2 } END { print s + 0 }' "$BACKUP_DIR/$name.counts")
        log "готово: $name.dump, $size, таблиц $tables, строк $rows, alembic ${src_alembic:-<нет>}, $(( $(date +%s) - started )) с"
        rc=0
        prune
    else
        mv "$part" "$BACKUP_DIR/$name.dump.unverified"
        log "ПРОВАЛ проверки восстановления, дамп оставлен как $name.dump.unverified"
        rc=1
    fi
    stop_check_cluster
    rm -f "$SRC_COUNTS"
    return $rc
}

check_file() {
    if verify "$1" "" ""; then
        log "$(basename "$1"): восстановление прошло, таблиц $(wc -l <"$CHECK_DIR/result.counts")"
        cat "$CHECK_DIR/result.counts"
        rc=0
    else
        log "$(basename "$1"): восстановление НЕ прошло"
        rc=1
    fi
    stop_check_cluster
    return $rc
}

locked() { flock -n 9 || { log "другой бэкап уже идёт"; return 1; }; "$@"; }

case "${1:-loop}" in
once)
    locked backup_once 9>"$LOCK"
    exit $?
    ;;
check)
    [ -f "${2:-}" ] || { echo "usage: $0 check FILE" >&2; exit 2; }
    locked check_file "$2" 9>"$LOCK"
    exit $?
    ;;
loop) ;;
*)
    echo "usage: $0 [loop | once | check FILE]" >&2
    exit 2
    ;;
esac

# PID 1 в контейнере: без явного trap SIGTERM игнорируется и docker stop ждёт 10 с.
trap 'stop_check_cluster; exit 0' TERM INT
pause() { sleep "$1" & wait $!; }

log "старт: каждые $INTERVAL_HOURS ч, хранить $KEEP, каталог $BACKUP_DIR"
while :; do
    last=$(cat "$STAMP" 2>/dev/null || echo 0)
    wait_s=$(( last + INTERVAL_HOURS * 3600 - $(date +%s) ))
    if [ "$wait_s" -gt 0 ]; then
        log "следующий бэкап через $(( wait_s / 60 )) мин"
        pause "$wait_s"
        continue
    fi
    if ! locked backup_once 9>"$LOCK"; then
        log "повтор через $RETRY_MINUTES мин"
        pause $(( RETRY_MINUTES * 60 ))
    fi
done
