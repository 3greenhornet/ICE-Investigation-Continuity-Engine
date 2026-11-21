#!/usr/bin/env python3
"""Run verify_part2/verify_part3 against a fresh embedded-Postgres database.

Provisions an ice_test_* database (via ice_test.make_dsn), applies the
vector extension + ICE migrations 012a-020, then runs both verify scripts
as subprocesses with DATABASE_URL pointing at it. Exit non-zero on any
script failure. This reproduces the environment the previous session used
(recorded in worklog: verify_part2/3 rc=0 offline).
"""
import asyncio
import os
import subprocess
import sys

sys.path.insert(0, "/home/z/my-project/scripts")
from ice_test import make_dsn  # noqa: E402

REPO = "/home/z/my-project/ice"
MIGRATIONS = [
    "012a_safety_check", "012_case_model", "013_evidence_graph",
    "014_case_events", "015_external_analysis_and_case_type",
    "016_contradiction_uniqueness", "017_proposed_edges",
    "018_reasoning_loop", "019_plan_and_workspace",
    "020_llm_ledger_and_mechanism_lab",
]


async def provision() -> str:
    import asyncpg

    dsn = await make_dsn()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        for m in MIGRATIONS:
            with open(f"{REPO}/db/migrations/{m}.sql") as fh:
                await conn.execute(fh.read())
    finally:
        await conn.close()
    return dsn


def main() -> int:
    dsn = asyncio.run(provision())
    env = os.environ.copy()
    env["DATABASE_URL"] = dsn
    env["PYTHONPATH"] = REPO
    fail = 0
    for script in ("scripts/verify_part2.py", "scripts/verify_part3.py"):
        rc = subprocess.call([sys.executable, script], cwd=REPO, env=env)
        name = os.path.basename(script)
        print(f"{name}: {'PASS' if rc == 0 else 'FAIL'} (rc={rc})")
        if rc != 0:
            fail = 1
    return fail


if __name__ == "__main__":
    sys.exit(main())
