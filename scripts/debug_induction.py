#!/usr/bin/env python3
"""Debug one benchmark case's deterministic induction (what fired and why)."""
import asyncio
import os
import sys

sys.path.insert(0, "/home/z/my-project/scripts")
sys.path.insert(0, "/home/z/my-project/ice")
from ice_test import make_dsn  # noqa: E402


async def main() -> None:
    dsn = await make_dsn()
    os.environ["DATABASE_URL"] = dsn
    sys.path.insert(0, "/home/z/my-project/ice")

    from db.connection import init_db
    await init_db()
    from db.connection import get_pool
    pool = await get_pool()
    root = "/home/z/my-project/ice"
    async with pool.acquire() as conn:
        already = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='cases')")
        if not already:
            for m in ["012a_safety_check", "012_case_model", "013_evidence_graph",
                      "014_case_events", "015_external_analysis_and_case_type",
                      "016_contradiction_uniqueness", "017_proposed_edges"]:
                with open(f"{root}/db/migrations/{m}.sql") as fh:
                    await conn.execute(fh.read())
        for m in ["018_reasoning_loop", "019_plan_and_workspace",
                  "020_llm_ledger_and_mechanism_lab"]:
            with open(f"{root}/db/migrations/{m}.sql") as fh:
                await conn.execute(fh.read())

    from benchmark.runner import load_case
    from case_engine.claim import extract_claims, get_claims_for_case
    from case_engine.evidence import get_active_evidence
    from case_engine.induction import _deterministic_candidates, _FAULT_TEMPLATES

    loaded = await load_case("/home/z/my-project/ice/benchmark/cases/case_01_latency")
    case_id = loaded["case_id"]
    for ev in await get_active_evidence(case_id):
        await extract_claims(ev.evidence_id)
    claims = [{"statement": c.statement} for c in await get_claims_for_case(case_id)]
    print(f"extracted {len(claims)} claims:")
    for c in claims:
        print("  -", c["statement"][:110])

    from case_engine.case import get_case
    objective = (await get_case(case_id)).objective
    corpus = " ".join([objective] + [c["statement"] for c in claims]).lower()
    print("\nobjective:", objective[:100])
    fired = []
    for tpl in _FAULT_TEMPLATES:
        matched = [s for s in tpl["signals"] if s in corpus]
        status = "FIRED" if matched else "----"
        fired.append((status, tpl["key"], matched[:4]))
    for s, k, m in fired:
        print(f"  {s:6s} {k:24s} {m}")

    cands = _deterministic_candidates(claims, objective)
    print(f"\n{len(cands)} candidates:")
    for c in cands:
        print("  -", c.statement[:100], "| score", c.score)


if __name__ == "__main__":
    asyncio.run(main())
