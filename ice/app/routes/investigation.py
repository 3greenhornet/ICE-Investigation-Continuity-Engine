"""app/routes/investigation.py — investigation endpoints (spec Part 4 §13.6).

POST /cases/{case_id}/investigate   run one full investigation cycle.
                                    AUDIT FIX #22: delegates to
                                    case_engine.cycle.run_investigation_cycle
                                    instead of inlining ~60 lines of engine
                                    logic (the ≤20-line handler rule).
                                    Part 2 §4.4: enable_auto_execute approves
                                    + executes the top action in-cycle.
GET  /cases/{case_id}/state         case summary (counts + leading + next)
GET  /cases/{case_id}/suggestions   endogenous "the system noticed"
                                    candidates (Part 2 §5.2)
GET  /cases/{case_id}/threads/debt  active investigation threads with debt
                                    scores (Part 2 §8.3)
GET  /cases/{case_id}/proposed-edges pending NLI-proposed claim→hypothesis
                                    edges awaiting review (Part 2 §10.4)
POST /proposed-edges/{edge_id}/accept  commit a proposed edge into
                                    evidence_graph_edges (Part 2 §10.4)
POST /proposed-edges/{edge_id}/reject  dismiss a proposed edge
GET  /cases/{case_id}/trace         case_events, newest first
GET  /cases/{case_id}/metrics       runtime metrics (improvisation B)
GET  /cases/{case_id}/consistency   consistency validation (improvisation C)

The Part 2 additions follow this file's established pattern of importing
the case_engine functions they delegate to directly (like
run_investigation_cycle / get_case_metrics / validate_case above).
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import CaseService, case_service
from app.schemas import (
    CaseEventResponse, CaseMetrics, CaseSummary, ConsistencyReport,
    DecisionSummary, EndogenousSuggestion, HypothesisChange,
    InvestigationResult, InvestigateRequest, LeadingHypothesisSummary,
    NextActionSummary,
)
from case_engine.cycle import run_investigation_cycle  # renamed from case_engine.investigation (merge spec Part 3 §0.6)
from case_engine.endogenous import collect_case_endogenous_candidates
from case_engine.evidence_graph import EdgeRelation, add_edge
from case_engine.exceptions import DatabaseUnavailableError
from case_engine.investigation_thread import InvestigationThreadRegistry
from case_engine.metrics import get_case_metrics
from case_engine.consistency import validate_case
from db.connection import get_pool

logger = logging.getLogger(__name__)

router = APIRouter()


def _to_result(cycle: dict) -> InvestigationResult:
    """Shape a run_investigation_cycle dict into the API response model.

    Part 2 §4.4/§7.2: the cycle dict now also carries lifecycle_changes and
    auto_executed_action; both pass through with tolerant .get() so older
    cycle shapes (and the pre-Part-2 shim) still validate.
    """
    leading = cycle.get("leading_hypothesis")
    recommended = cycle.get("recommended_action")
    return InvestigationResult(
        case_id=cycle["case_id"],
        leading_hypothesis=(
            LeadingHypothesisSummary.model_validate(leading) if leading else None
        ),
        hypothesis_changes=[
            HypothesisChange.model_validate(c) for c in cycle["hypothesis_changes"]
        ],
        new_contradictions=cycle["new_contradictions"],
        resolved_contradictions=cycle["resolved_contradictions"],
        new_gaps=cycle["new_gaps"],
        new_actions=cycle["new_actions"],
        recommended_action=(
            NextActionSummary.model_validate(recommended) if recommended else None
        ),
        decision=DecisionSummary.model_validate(cycle["decision"]),
        events_emitted=cycle["events_emitted"],
        latency_seconds=cycle["latency_seconds"],
        lifecycle_changes=cycle.get("lifecycle_changes", []),
        auto_executed_action=cycle.get("auto_executed_action"),
    )


@router.post("/cases/{case_id}/investigate", response_model=InvestigationResult)
async def investigate_route(case_id: str, payload: InvestigateRequest):
    cycle = await run_investigation_cycle(
        case_id,
        enable_next_best_action=payload.enable_next_best_action,
        enable_falsification=payload.enable_falsification,
        enable_auto_execute=payload.enable_auto_execute,  # Part 2 §4.4
    )
    return _to_result(cycle)


@router.get("/cases/{case_id}/state", response_model=CaseSummary)
async def case_state_route(case_id: str, svc: CaseService = Depends(case_service)):
    return CaseSummary.model_validate(await svc.summary(case_id))


@router.get(
    "/cases/{case_id}/suggestions", response_model=list[EndogenousSuggestion]
)
async def suggestions_route(case_id: str):
    """Endogenous "the system noticed" candidates (Part 2 §5.2).

    Delegates to collect_case_endogenous_candidates (Part 2 §5.1): aged
    investigation threads, uncited evidence, stuck hypotheses and aged
    contradictions. Raises DatabaseUnavailableError (→ 503 via ERROR_MAP)
    when the pool is down — an empty list genuinely means "nothing needs
    attention".
    """
    candidates = await collect_case_endogenous_candidates(case_id, current_turn=0)
    return [EndogenousSuggestion.model_validate(c) for c in candidates]


@router.get("/cases/{case_id}/threads/debt", response_model=list[dict])
async def threads_debt_route(case_id: str):
    """Active investigation threads with their debt scores (Part 2 §8.3).

    debt = strength × min(age_days, 30) × decision_impact × uncertainty
    (InvestigationThreadRegistry.compute_debt), sorted by debt descending.
    The registry's _ensure_loaded raises DatabaseUnavailableError (→ 503
    via ERROR_MAP) when the pool is down.
    """
    registry = InvestigationThreadRegistry(case_id)
    await registry._ensure_loaded()
    threads = await registry.get_active_by_debt(limit=20)
    now = datetime.now(timezone.utc)
    return [
        {
            "thread_id": t.thread_id,
            "question": t.question,
            "debt": registry.compute_debt(t),
            "strength": t.strength,
            "age_days": (now - t.last_progress_at).total_seconds() / 86400,
            "decision_impact": t.decision_impact,
            "uncertainty": t.uncertainty,
        }
        for t in threads
    ]


@router.get("/cases/{case_id}/proposed-edges", response_model=list[dict])
async def list_proposed_edges_route(case_id: str):
    """Pending NLI-proposed claim→hypothesis edges, newest first (§10.4)."""
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM proposed_edges
            WHERE case_id = $1 AND status = 'pending'
            ORDER BY created_at DESC
            """,
            case_id,
        )
    return [dict(r) for r in rows]


@router.post("/proposed-edges/{edge_id}/accept", response_model=dict)
async def accept_proposed_edge_route(edge_id: str):
    """Accept a proposed edge: write the real evidence_graph_edges row.

    Error handling: pool-down raises DatabaseUnavailableError (→ 503
    DATABASE_UNAVAILABLE via ERROR_MAP). For missing/non-pending edges we
    keep the doc's plain HTTPException (404/400) — proposed_edges has no
    typed case_engine exception and inventing one for this thin review
    surface would grow ERROR_MAP without engine buy-in.
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot accept proposed edge: no DB pool")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM proposed_edges WHERE edge_id = $1", edge_id
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Proposed edge not found")
    if row["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Edge already {row['status']}")

    relation = {
        "supports": EdgeRelation.SUPPORTS,
        "contradicts": EdgeRelation.CONTRADICTS,
    }.get(row["relation"])
    if relation is None:
        raise HTTPException(status_code=400, detail="Unknown relation")

    await add_edge(
        case_id=row["case_id"],
        from_node=row["from_node"],
        from_type=row["from_type"],
        to_node=row["to_node"],
        to_type=row["to_type"],
        relation=relation,
        weight=row["confidence"],
    )

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE proposed_edges
            SET status = 'accepted', resolved_at = NOW()
            WHERE edge_id = $1
            """,
            edge_id,
        )

    # EVENT DISCIPLINE (doc §10.4 option 2 — deliberate deviation): no
    # case_events row is emitted here; proposed_edge_accepted is not in
    # the EVENT_TYPES allow-list and adding it is a schema change deferred
    # to Part 4. Logged at INFO instead.
    logger.info(
        "proposed_edge_accepted: %s (%s %s -> %s, conf %.2f)",
        edge_id, row["relation"], row["from_node"], row["to_node"],
        row["confidence"],
    )
    return {"status": "accepted", "edge_id": edge_id}


@router.post("/proposed-edges/{edge_id}/reject", response_model=dict)
async def reject_proposed_edge_route(edge_id: str):
    """Reject a proposed edge: mark it rejected without touching the graph."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot reject proposed edge: no DB pool")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM proposed_edges WHERE edge_id = $1", edge_id
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Proposed edge not found")
    if row["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Edge already {row['status']}")

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE proposed_edges
            SET status = 'rejected', resolved_at = NOW()
            WHERE edge_id = $1
            """,
            edge_id,
        )

    # EVENT DISCIPLINE (doc §10.4 option 2 — deliberate deviation): INFO
    # log instead of a case_events row (see accept route above).
    logger.info("proposed_edge_rejected: %s", edge_id)
    return {"status": "rejected", "edge_id": edge_id}


@router.get("/cases/{case_id}/metrics", response_model=CaseMetrics)
async def case_metrics_route(case_id: str):
    return CaseMetrics.model_validate(await get_case_metrics(case_id))


@router.get("/cases/{case_id}/consistency", response_model=ConsistencyReport)
async def case_consistency_route(case_id: str):
    violations = await validate_case(case_id)
    return ConsistencyReport(violations=violations)


@router.get("/cases/{case_id}/trace", response_model=list[CaseEventResponse])
async def case_trace_route(case_id: str, limit: int = Query(100, ge=1, le=1000)):
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM case_events
            WHERE case_id = $1
            ORDER BY event_seq DESC
            LIMIT $2
            """,
            case_id, limit,
        )
    return [CaseEventResponse.model_validate(dict(r)) for r in rows]