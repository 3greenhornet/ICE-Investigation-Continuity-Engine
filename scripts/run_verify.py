#!/usr/bin/env python3
"""Run the repo's verify_part2/verify_part3 acceptance scripts against a
fresh embedded postgres with migrations applied (AGENTS.md acceptance)."""
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
    "021_project_goals",
    "022_goal_attributes",
]


async def main() -> int:
    dsn = await make_dsn()
    os.environ["DATABASE_URL"] = dsn
    sys.path.insert(0, REPO)

    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        for m in MIGRATIONS:
            with open(f"{REPO}/db/migrations/{m}.sql") as fh:
                await conn.execute(fh.read())
        print("migrations applied")
    finally:
        await conn.close()

    env = os.environ.copy()
    env["PYTHONPATH"] = REPO
    rc = 0
    for script in ("scripts/verify_part2.py", "scripts/verify_part3.py"):
        r = subprocess.run(
            [sys.executable, script], cwd=REPO, env=env,
            capture_output=True, text=True, timeout=600,
        )
        print(f"\n===== {script} rc={r.returncode} =====")
        print((r.stdout or "")[-2000:])
        if r.returncode != 0:
            print("STDERR tail:", (r.stderr or "")[-1200:])
            rc = r.returncode
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
