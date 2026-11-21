#!/bin/bash
# Run pytest inside the ICE repo with the embedded postgres DSN.
export DATABASE_URL="${DATABASE_URL:-postgresql://postgres:@/postgres?host=/tmp/ice_pgdata}"
cd /home/z/my-project/ice || exit 1
exec python -m pytest "$@"
