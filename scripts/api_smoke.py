#!/usr/bin/env python3
"""API smoke test: boot the FastAPI app against the embedded postgres,
drive the new reasoning endpoints end-to-end."""
import asyncio
import os
import sys

from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from ice_test import make_dsn  # noqa: E402

REPO = os.environ.get("ICE_HOME") or str(_ROOT / "ice")
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
    finally:
        await conn.close()

    from httpx import AsyncClient, ASGITransport
    from app.main import app

    base = "http://test/api/v1"
    async with AsyncClient(transport=ASGITransport(app=app), base_url=base) as c:
        # create a case
        r = await c.post("/cases", json={
            "title": "API smoke case",
            "objective": "Diagnose the checkout latency regression after deployment v42",
        })
        assert r.status_code == 201, r.text
        case_id = r.json()["case_id"]
        print("case created:", case_id)

        # ingest evidence
        r = await c.post(f"/cases/{case_id}/evidence", json={
            "content": ("The checkout service p95 latency regressed after the v42 "
                        "deployment. The connection pool was saturated; requests "
                        "queued. max_connections was reduced from 100 to 20."),
        })
        assert r.status_code == 201, r.text
        ev_id = r.json()["evidence_id"]
        print("evidence ingested:", ev_id)

        # run the cycle WITH induction + workspace
        r = await c.post(
            f"/cases/{case_id}/cycle",
            params={"enable_induction": True, "enable_workspace": True},
        )
        assert r.status_code == 200, r.text
        cycle = r.json()
        print("cycle ok: induction path =",
              (cycle.get("induction") or {}).get("generation_path"),
              "| hypotheses created =",
              len((cycle.get("induction") or {}).get("created_hypothesis_ids") or []))
        print("workspace selected =",
              len((cycle.get("workspace") or {}).get("selected") or []))

        # predictions exist
        r = await c.get(f"/cases/{case_id}/predictions")
        assert r.status_code == 200
        preds = r.json()
        print("predictions:", len(preds))

        # hybrid search
        r = await c.get(f"/cases/{case_id}/search",
                        params={"q": "connection pool saturation"})
        assert r.status_code == 200
        print("search hits:", len(r.json()))

        # timeline
        r = await c.get(f"/cases/{case_id}/timeline")
        assert r.status_code == 200
        print("timeline entries:", len(r.json()))

        # ACH
        r = await c.get(f"/cases/{case_id}/ach")
        assert r.status_code == 200
        print("ACH hypotheses:", len(r.json()["hypotheses"]))

        # plan lifecycle
        r = await c.post(f"/cases/{case_id}/plan",
                         params={"description": "Inspect pool configuration"})
        assert r.status_code == 200, r.text
        item_id = r.json()["plan_item_id"]
        r = await c.post(f"/plan-items/{item_id}/status",
                         params={"status": "in_progress", "note": "started"})
        assert r.status_code == 200, r.text
        r = await c.post(f"/cases/{case_id}/plan/reconcile")
        assert r.status_code == 200, r.text
        print("plan reconcile:", r.json()["summary"])

        # introspection + trajectory + credibility + mechanisms
        for path in (
            f"/cases/{case_id}/introspection",
            f"/cases/{case_id}/trajectory",
            f"/evidence/{ev_id}/credibility",
            "/mechanisms",
            f"/cases/{case_id}/llm-usage",
        ):
            r = await c.get(path)
            assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"
        print("introspection/trajectory/credibility/mechanisms/llm-usage OK")

        # contrastive search
        r = await c.get(f"/cases/{case_id}/contrastive",
                        params={"q": "pool exhaustion caused latency"})
        assert r.status_code == 200
        print("contrastive hits:", len(r.json()))

        # ---- goal system (Evolving Project Model) ----
        # a NEED (reconcilable kind) + a quality objective
        r = await c.post(f"/cases/{case_id}/goals", json={
            "statement": "Latency must return under 200ms",
            "kind": "need", "clarity_level": 3,
        })
        assert r.status_code == 200, r.text
        goal_id = r.json()["goal_id"]
        print("goal created:", goal_id, "clarity",
              r.json()["clarity"])

        # vague Level-1 goal + decomposition (GORE-lite proposals)
        r = await c.post(f"/cases/{case_id}/goals", json={
            "statement": "Maybe cache pool config lookups",
            "kind": "alternative", "clarity_level": 1,
            "origin": "ice_proposal",
        })
        assert r.status_code == 200, r.text
        alt_id = r.json()["goal_id"]

        r = await c.post(f"/goals/{goal_id}/decompose")
        assert r.status_code == 200, r.text
        print("decomposition proposed:",
              len(r.json()["proposed_open_questions"]), "questions")

        # user decision promotes the ICE proposal to developer_confirmed
        r = await c.post(f"/goals/{alt_id}/status",
                         json={"status": "active", "decided_by": "user",
                               "note": "worth a spike"})
        assert r.status_code == 200, r.text
        assert r.json()["origin"] == "developer_confirmed", r.text
        print("origin promotion ice_proposal -> developer_confirmed OK")

        # intent profile + reconciliation
        r = await c.get(f"/cases/{case_id}/intent-profile")
        assert r.status_code == 200, r.text
        print("intent profile slots:", r.json()["summary"]["slots_filled"])
        r = await c.get(f"/cases/{case_id}/goal-reconciliation")
        assert r.status_code == 200, r.text
        assert r.json()["summary"]["goals_checked"] >= 1, r.text
        print("goal reconciliation:", r.json()["summary"])

        # ---- research mode (STORM-lite) ----
        # a claim so the synthesis outline has tiers to separate
        r = await c.post("/claims", json={
            "case_id": case_id,
            "statement": "The v42 deployment reduced max_connections",
            "claim_type": "OBSERVATION",
        })
        assert r.status_code == 201, r.text

        r = await c.get(f"/cases/{case_id}/perspectives")
        assert r.status_code == 200, r.text
        print("perspectives:", r.json()["summary"]["total"],
              "unexplored:", r.json()["summary"]["unexplored"])

        r = await c.post(f"/cases/{case_id}/research-questions",
                         params={"max_gaps": 3})
        assert r.status_code == 200, r.text
        print("research questions -> gaps:",
              len(r.json()["gaps_created"]))

        r = await c.get(f"/cases/{case_id}/research-outline")
        assert r.status_code == 200, r.text
        assert r.json()["summary"]["established"] >= 1, r.text
        print("research outline tiers:", r.json()["summary"][
            "established"], "established /",
            r.json()["summary"]["contested"], "contested /",
            r.json()["summary"]["interpreted"], "interpreted")

    print("\nAPI SMOKE TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
