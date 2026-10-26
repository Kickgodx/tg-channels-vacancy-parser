# Postgres 16 (alpine, как раньше) + расширение pgvector для эмбеддингов вакансий и резюме.
# Alpine, а не образ pgvector/pgvector (debian): тот же musl и правила сортировки строк, что у
# уже инициализированного тома pgdata, — смена образа не требует REINDEX.
FROM postgres:16-alpine

ARG PGVECTOR_VERSION=0.8.7

# with_llvm=no — без JIT-bitcode (нужен clang той же версии, что у сборки Postgres);
# OPTFLAGS="" — без -march=native, образ переносим между машинами.
RUN apk add --no-cache --virtual .pgvector-build git build-base \
 && git clone --branch "v${PGVECTOR_VERSION}" --depth 1 https://github.com/pgvector/pgvector.git /tmp/pgvector \
 && make -C /tmp/pgvector OPTFLAGS="" with_llvm=no \
 && make -C /tmp/pgvector OPTFLAGS="" with_llvm=no install \
 && rm -rf /tmp/pgvector \
 && apk del .pgvector-build
