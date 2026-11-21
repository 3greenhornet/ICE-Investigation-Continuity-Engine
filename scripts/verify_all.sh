#!/bin/bash
# verify_all.sh — one-shot full verification of the ICE improvements pass.
#
# Idempotently brings up the embedded Postgres (the platform may reap the
# daemon between tool calls), then runs the complete chain:
#   1. full test suite (unit + integration; eval skips without LLM keys)
#   2. scripts/verify_part2.py and scripts/verify_part3.py (exit codes)
#   3. offline benchmark, honest + legacy modes (fault-class aggregate)
#   4. API smoke test
# Any failure aborts with a non-zero exit; the last line is a summary.
set -uo pipefail

PG_BIN=/home/z/.venv/lib/python3.12/site-packages/pgserver/pginstall/bin
export DATABASE_URL='postgresql://postgres:@/postgres?host=/tmp'
export PYTHONPATH=/home/z/my-project/ice
cd /home/z/my-project/ice || exit 1

if ! "$PG_BIN/pg_isready" -q -h /tmp -p 5432 2>/dev/null; then
    echo "[pg] not ready — starting embedded server"
    "$PG_BIN/pg_ctl" -D /tmp/ice_pgdata -l /tmp/ice_pg.log -w start >/dev/null \
        || { echo "[pg] START FAILED"; exit 1; }
fi
"$PG_BIN/psql" "$DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null 2>&1 \
    || echo "[pg] vector extension create failed (may already exist)"

fail=0

echo "== [1/4] test suite =="
python -m pytest -q 2>&1 | tail -1 || fail=1

echo "== [2/4] verify_part2 / verify_part3 =="
python scripts/verify_part2.py >/dev/null 2>&1 && echo "verify_part2: PASS" || { echo "verify_part2: FAIL"; fail=1; }
python scripts/verify_part3.py >/dev/null 2>&1 && echo "verify_part3: PASS" || { echo "verify_part3: FAIL"; fail=1; }

echo "== [3/4] benchmark (honest + legacy) =="
timeout 280 python scripts/run_benchmark_offline.py --legacy 2>&1 \
    | grep -E "hypothesis_fault_class_accuracy|hypothesis_top1_accuracy|decision_correctness" \
    || fail=1

echo "== [4/4] API smoke =="
timeout 200 python /home/z/my-project/scripts/api_smoke.py 2>&1 | tail -1 || fail=1

if [ "$fail" -eq 0 ]; then
    echo "VERIFY_ALL: ALL PASS"
else
    echo "VERIFY_ALL: FAILURES PRESENT"
    exit 1
fi
