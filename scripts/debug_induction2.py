#!/usr/bin/env python3
"""Debug: check induction edges + confidence differentiation."""
import asyncio
import os
import sys

sys.path.insert(0, "/home/z/my-project/scripts")
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
    from case_engine.induction import induce_hypotheses
    from case_engine.hypothesis import (
        list_hypotheses_for_case, get_current_version,
        recompute_hypothesis_confidence,
    )

    loaded = await load_case(f"{root}/benchmark/cases/case_01_latency")
    case_id = loaded["case_id"]
    for ev in await get_active_evidence(case_id):
        await extract_claims(ev.evidence_id)
    print("claims:", len(await get_claims_for_case(case_id)))

    result = await induce_hypotheses(case_id, create=True)
    print("induction path:", result["generation_path"])
    print("candidates:", len(result["candidates"]),
          "created:", len(result["created_hypothesis_ids"]),
          "deduped:", result["deduplicated"])
    for c in result["candidates"]:
        print("  cand:", c["statement"][:100], "| score:", c["score"],
              "| support:", len(c["supporting_observations"]))

    for h in await list_hypotheses_for_case(case_id):
        await recompute_hypothesis_confidence(h.hypothesis_id, "debug")
        v = await get_current_version(h.hypothesis_id)
        print("HYP:", h.statement[:80], "| conf:", round(v.confidence, 3))

    async with pool.acquire() as conn:
        edges = await conn.fetch(
            "SELECT from_node, to_node, relation, weight, metadata "
            "FROM evidence_graph_edges WHERE case_id = $1 "
            "AND metadata->>'origin' = 'ice_induction'", case_id)
        print("induction edges:", len(edges))
        for e in edges[:6]:
            print("  edge", e["from_node"], "->", e["to_node"],
                  e["relation"], "w=", e["weight"])


if __name__ == "__main__":
    asyncio.run(main())
