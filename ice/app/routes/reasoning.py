"""app/routes/reasoning.py — HTTP surface for the reasoning-loop additions.

Owner:      Hari ICE "addition and improvements" pass (P1-P5 projections).
Endpoints (all read-only or explicitly operator-driven):
    POST /cases/{case_id}/induce                 run hypothesis induction
    POST /cases/{case_id}/cycle                  run the full cycle with
                                                 induction/workspace flags
    GET  /cases/{case_id}/predictions            list prediction records
    POST /predictions/{id}/resolve               resolve one (operator)
    GET  /cases/{case_id}/calibration            prediction calibration report
    GET  /cases/{case_id}/timeline               reconstructed timeline
    GET  /cases/{case_id}/state-change           healthy->failing transition
    GET  /cases/{case_id}/ach                    ACH matrix + diagnostics
    GET  /cases/{case_id}/causal-edges           proposed/accepted edges
    POST /causal-edges/{id}/review               accept/reject an edge
    POST /cases/{case_id}/causal-edges/from-timeline  propose PRECEDES edges
    GET  /cases/{case_id}/workspace              persisted workspace items
    POST /cases/{case_id}/workspace/run          one workspace cycle
    GET  /cases/{case_id}/search?q=              hybrid evidence search
    GET  /cases/{case_id}/contrastive?q=         counterevidence search
    GET  /cases/{case_id}/plan                   plan items + revisions
    POST /cases/{case_id}/plan                   add a plan item
    POST /cases/{case_id}/plan/reconcile         plan-vs-implementation report
    POST /plan-items/{id}/status                 transition a plan item
    GET  /cases/{case_id}/introspection          flow report (process mining)
    GET  /cases/{case_id}/trajectory             first-failure localization
    GET  /cases/{case_id}/memory-diagnostics     EvalMem-style report
    GET  /cases/{case_id}/llm-usage              token/cost ledger summary
    GET  /mechanisms                             research ledger catalogue
    GET  /cases/{case_id}/credibility/{evidence_id}  five-axis credibility
"""

from typing import Optional

from fastapi import APIRouter, Query

router = APIRouter()

# These routes call case_engine directly (the established pattern from
# app/routes/investigation.py for spec-mandated specialists); response
# bodies are the engines' dict/dataclass shapes.


@router.post("/cases/{case_id}/induce")
async def induce_route(case_id: str, max_candidates: int = Query(6, ge=1, le=12)):
    from case_engine.induction import induce_hypotheses

    return await induce_hypotheses(case_id, max_candidates=max_candidates, create=True)


@router.post("/cases/{case_id}/cycle")
async def cycle_route(
    case_id: str,
    enable_induction: bool = Query(False),
    enable_workspace: bool = Query(False),
    enable_falsification: bool = Query(False),
    enable_auto_execute: bool = Query(False),
):
    from case_engine.cycle import run_investigation_cycle

    return await run_investigation_cycle(
        case_id,
        enable_induction=enable_induction,
        enable_workspace=enable_workspace,
        enable_falsification=enable_falsification,
        enable_auto_execute=enable_auto_execute,
    )


@router.get("/cases/{case_id}/predictions")
async def list_predictions_route(
    case_id: str, status: Optional[str] = Query(None)
):
    from case_engine.predictions import list_predictions

    return await list_predictions(case_id=case_id, status=status)


@router.post("/predictions/{prediction_id}/resolve")
async def resolve_prediction_route(
    prediction_id: str, status: str, observed_result: str
):
    from case_engine.predictions import resolve_prediction

    return await resolve_prediction(prediction_id, status, observed_result)


@router.get("/cases/{case_id}/calibration")
async def calibration_route(case_id: str):
    from case_engine.predictions import prediction_calibration_report

    return await prediction_calibration_report(case_id)


@router.get("/cases/{case_id}/timeline")
async def timeline_route(case_id: str):
    from case_engine.timeline import build_case_timeline

    entries = await build_case_timeline(case_id)
    return [e.to_dict() for e in entries]


@router.get("/cases/{case_id}/state-change")
async def state_change_route(case_id: str):
    from case_engine.timeline import explain_state_change

    return await explain_state_change(case_id)


@router.get("/cases/{case_id}/ach")
async def ach_route(case_id: str):
    from case_engine.causal import build_ach_matrix

    return await build_ach_matrix(case_id)


@router.get("/cases/{case_id}/causal-edges")
async def list_causal_edges_route(case_id: str, status: Optional[str] = None):
    from case_engine.causal import list_causal_edges

    return await list_causal_edges(case_id, status=status)


@router.post("/causal-edges/{causal_edge_id}/review")
async def review_causal_edge_route(
    causal_edge_id: str, decision: str, note: str = ""
):
    from case_engine.causal import review_causal_edge

    return await review_causal_edge(causal_edge_id, decision, note)


@router.post("/cases/{case_id}/causal-edges/from-timeline")
async def causal_from_timeline_route(case_id: str):
    from case_engine.causal import propose_edges_from_timeline

    edges = await propose_edges_from_timeline(case_id)
    return {"proposed": len(edges), "edges": edges}


@router.get("/cases/{case_id}/workspace")
async def get_workspace_route(case_id: str, disposition: Optional[str] = None):
    from case_engine.workspace import get_workspace

    return await get_workspace(case_id, disposition=disposition)


@router.post("/cases/{case_id}/workspace/run")
async def run_workspace_route(case_id: str, budget: int = Query(7, ge=1, le=20)):
    from case_engine.workspace import run_workspace_cycle

    return await run_workspace_cycle(case_id, budget=budget)


@router.get("/cases/{case_id}/search")
async def search_route(
    case_id: str,
    q: str = Query(..., min_length=2),
    limit: int = Query(10, ge=1, le=50),
    cross_case: bool = Query(False),
):
    from case_engine.retrieval import search_evidence

    hits = await search_evidence(
        q, case_id=case_id, limit=limit, cross_case=cross_case
    )
    return [h.to_dict() for h in hits]


@router.get("/cases/{case_id}/contrastive")
async def contrastive_route(
    case_id: str, q: str = Query(..., min_length=2), limit: int = Query(8)
):
    from case_engine.retrieval import contrastive_search

    hits = await contrastive_search(q, case_id=case_id, limit=limit)
    return [h.to_dict() for h in hits]


@router.get("/cases/{case_id}/plan")
async def get_plan_route(case_id: str):
    from case_engine.plan import list_plan_items, list_plan_revisions

    return {
        "items": await list_plan_items(case_id),
        "revisions": await list_plan_revisions(case_id),
    }


@router.post("/cases/{case_id}/plan")
async def add_plan_item_route(
    case_id: str,
    description: str,
    objective_ref: Optional[str] = None,
    depends_on: Optional[list[str]] = None,
):
    from case_engine.plan import create_plan_item

    return await create_plan_item(
        case_id, description, objective_ref=objective_ref, depends_on=depends_on
    )


@router.post("/cases/{case_id}/plan/reconcile")
async def reconcile_plan_route(case_id: str):
    from case_engine.plan import reconcile_plan

    return await reconcile_plan(case_id)


@router.post("/plan-items/{plan_item_id}/status")
async def plan_status_route(
    plan_item_id: str,
    status: str,
    note: str = "",
    decided_by: str = "user",
):
    from case_engine.plan import update_plan_status

    return await update_plan_status(
        plan_item_id, status, note=note, decided_by=decided_by
    )


@router.get("/cases/{case_id}/introspection")
async def introspection_route(case_id: str):
    from case_engine.introspection import investigation_flow_report

    return await investigation_flow_report(case_id)


@router.get("/cases/{case_id}/trajectory")
async def trajectory_route(case_id: str):
    from case_engine.trajectory import localize_first_failure

    return await localize_first_failure(case_id)


@router.post("/cases/{case_id}/memory-diagnostics")
async def memory_diagnostics_route(case_id: str, facts: list[str]):
    from case_engine.diagnostics import memory_diagnostic_report

    return await memory_diagnostic_report(case_id, facts)


@router.get("/cases/{case_id}/llm-usage")
async def llm_usage_route(case_id: str):
    from case_engine.llm import get_llm_usage

    return await get_llm_usage(case_id)


@router.get("/mechanisms")
async def mechanisms_route(decision: Optional[str] = None):
    from case_engine.mechanism_lab import list_mechanisms, seed_mechanism_lab

    try:
        await seed_mechanism_lab()
    except Exception:
        pass
    return await list_mechanisms(decision=decision)


@router.get("/evidence/{evidence_id}/credibility")
async def credibility_route(evidence_id: str):
    from case_engine.evidence import assess_evidence_credibility

    return await assess_evidence_credibility(evidence_id)
