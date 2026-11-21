#!/bin/bash
# ensure_pg.sh — idempotent guard: start the embedded Postgres if reaped.
PG_BIN=/home/z/.venv/lib/python3.12/site-packages/pgserver/pginstall/bin
if ! "$PG_BIN/pg_isready" -q -h /tmp -p 5432 2>/dev/null; then
    "$PG_BIN/pg_ctl" -D /tmp/ice_pgdata -l /tmp/ice_pg.log -w start >/dev/null 2>&1 || true
    for _ in 1 2 3 4 5; do
        "$PG_BIN/pg_isready" -q -h /tmp -p 5432 2>/dev/null && break
        sleep 1
    done
fi
"$PG_BIN/pg_isready" -h /tmp -p 5432
