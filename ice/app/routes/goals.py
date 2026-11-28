"""app/routes/goals.py — HTTP surface for the goal system + research mode.

Owner:      Hari ICE "addition and improvements" pass, goal-system phase.
Endpoints (the Evolving Project Model — intent graph, clarity, provenance,
supersede chains, maturity, reconciliation, intent discovery — plus the
STORM-lite research workflow):

    POST /cases/{case_id}/goals                  record an intent node
    GET  /cases/{case_id}/goals                  list (kind filter,
                                                 superseded visible)
    GET  /goals/{goal_id}                        fetch one
    POST /goals/{goal_id}/status                 status transition; the
                                                 user path promotes
                                                 ice_inference/ice_proposal
                                                 to developer_confirmed
    POST /goals/{goal_id}/supersede              revise: new row, old
                                                 crossed out (never deleted)
    POST /goals/{goal_id}/edges                  connect (typed relation)
    GET  /goals/{goal_id}/dependencies           what depends on it / it on
    POST /goals/{goal_id}/maturity               evidence-backed maturity
    GET  /goals/{goal_id}/maturity               latest assessment
    POST /goals/{goal_id}/decompose              GORE-lite proposal
    GET  /cases/{case_id}/intent-profile         ICE's current understanding
                                                 INCLUDING its gaps
    GET  /cases/{case_id}/goal-reconciliation    intent vs implementation
                                                 report (proposal, not
                                                 mutation)
    GET  /cases/{case_id}/perspectives           STORM-lite perspectives
    POST /cases/{case_id}/research-questions     questions -> information
                                                 gaps
    GET  /cases/{case_id}/research-outline       tiered synthesis outline
    POST /cases/{case_id}/research-brief         the whole pass at once

These routes call case_engine directly (the established pattern from
app/routes/reasoning.py); response bodies are the engine's dict shapes.
"""

from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter()


class GoalCreateRequest(BaseModel):
    statement: str
    kind: str = "goal"
    clarity_level: int = 1
    origin: str = "developer_statement"
    rationale: Optional[str] = None
    supersedes: Optional[str] = None
    priority: Optional[int] = None
    success_criteria: Optional[str] = None
    tags: Optional[list[str]] = None
    target_date: Optional[str] = None  # ISO date
    metadata: Optional[dict] = None


class GoalReviseRequest(BaseModel):
    statement: Optional[str] = None
    kind: Optional[str] = None
    clarity_level: Optional[int] = None
    rationale: Optional[str] = None
    priority: Optional[int] = None
    success_criteria: Optional[str] = None
    tags: Optional[list[str]] = None
    target_date: Optional[str] = None
    metadata: Optional[dict] = None


class GoalStatusRequest(BaseModel):
    status: str
    decided_by: str = "user"
    note: str = ""


class GoalSupersedeRequest(BaseModel):
    statement: str
    rationale: str = ""
    origin: str = "developer_statement"
    clarity_level: Optional[int] = None


class GoalEdgeRequest(BaseModel):
    to_goal: str
    relation: str
    provenance: str = "ice_inference"
    note: Optional[str] = None


class GoalMaturityRequest(BaseModel):
    maturity: str
    evidence_ids: list[str] = []
    assessed_by: str = "ice"
    note: str = ""


# ---------------------------------------------------------------------------
# Goal CRUD + supersede chains
# ---------------------------------------------------------------------------


@router.post("/cases/{case_id}/goals")
async def create_goal_route(case_id: str, payload: GoalCreateRequest):
    from case_engine.goals import create_goal

    return await create_goal(
        case_id=case_id,
        statement=payload.statement,
        kind=payload.kind,
        clarity_level=payload.clarity_level,
        origin=payload.origin,
        rationale=payload.rationale,
        supersedes=payload.supersedes,
        priority=payload.priority,
        success_criteria=payload.success_criteria,
        tags=payload.tags,
        target_date=payload.target_date,
        metadata=payload.metadata,
    )


@router.post("/goals/{goal_id}/revise")
async def revise_goal_route(goal_id: str, payload: GoalReviseRequest):
    """New version of a goal with the given fields changed (old one superseded)."""
    from case_engine.goals import revise_goal

    changes = {k: v for k, v in payload.model_dump().items() if v is not None}
    return await revise_goal(goal_id, **changes)


@router.post("/goals/{goal_id}/maturity/sync")
async def sync_goal_maturity_route(goal_id: str, repo: Optional[str] = Query(None)):
    """Evidence-backed maturity from RepoIntel's knowledge base (repo = name or id)."""
    from case_engine.goals import sync_maturity_from_code

    return await sync_maturity_from_code(goal_id, repo=repo)


@router.get("/cases/{case_id}/goals")
async def list_goals_route(
    case_id: str,
    kind: Optional[str] = Query(None),
    include_superseded: bool = Query(True),
):
    from case_engine.goals import list_goals

    return await list_goals(
        case_id, kind=kind, include_superseded=include_superseded
    )


@router.get("/goals/{goal_id}")
async def get_goal_route(goal_id: str):
    from case_engine.goals import get_goal

    return await get_goal(goal_id)


@router.post("/goals/{goal_id}/status")
async def set_goal_status_route(
    goal_id: str, payload: GoalStatusRequest
):
    from case_engine.goals import set_goal_status

    return await set_goal_status(
        goal_id, payload.status, decided_by=payload.decided_by,
        note=payload.note,
    )


@router.post("/goals/{goal_id}/supersede")
async def supersede_goal_route(
    goal_id: str, payload: GoalSupersedeRequest
):
    from case_engine.goals import create_goal, get_goal

    current = await get_goal(goal_id)
    return await create_goal(
        case_id=current["case_id"],
        statement=payload.statement,
        kind=current["kind"],
        clarity_level=payload.clarity_level or current["clarity_level"],
        origin=payload.origin,
        rationale=payload.rationale,
        supersedes=goal_id,
    )


# ---------------------------------------------------------------------------
# Typed edges + dependencies
# ---------------------------------------------------------------------------


@router.post("/goals/{goal_id}/edges")
async def add_goal_edge_route(goal_id: str, payload: GoalEdgeRequest):
    from case_engine.goals import connect_goals

    return await connect_goals(
        from_goal=goal_id, to_goal=payload.to_goal,
        relation=payload.relation, provenance=payload.provenance,
        note=payload.note,
    )


@router.get("/cases/{case_id}/goal-edges")
async def list_goal_edges_route(case_id: str):
    from case_engine.goals import list_goal_edges

    return await list_goal_edges(case_id)


@router.get("/goals/{goal_id}/dependencies")
async def goal_dependencies_route(goal_id: str):
    from case_engine.goals import goal_dependency_breadth

    return await goal_dependency_breadth(goal_id)


# ---------------------------------------------------------------------------
# Maturity + decomposition + intent profile + reconciliation
# ---------------------------------------------------------------------------


@router.post("/goals/{goal_id}/maturity")
async def assess_maturity_route(goal_id: str, payload: GoalMaturityRequest):
    from case_engine.goals import assess_maturity

    return await assess_maturity(
        goal_id, payload.maturity, evidence_ids=payload.evidence_ids,
        assessed_by=payload.assessed_by, note=payload.note,
    )


@router.get("/goals/{goal_id}/maturity")
async def latest_maturity_route(goal_id: str):
    from case_engine.goals import latest_maturity

    return (await latest_maturity(goal_id)) or {"maturity": "no_candidate"}


@router.post("/goals/{goal_id}/decompose")
async def decompose_goal_route(goal_id: str):
    from case_engine.goals import decompose_goal

    return await decompose_goal(goal_id)


@router.post("/cases/{case_id}/goals/adjacent-proposals")
async def adjacent_goals_route(
    case_id: str,
    repo: Optional[str] = Query(None),
    max_proposals: int = Query(4, ge=1, le=8),
):
    from case_engine.goals import propose_adjacent_goals

    return await propose_adjacent_goals(case_id, repo=repo, max_proposals=max_proposals)


@router.get("/cases/{case_id}/intent-profile")
async def intent_profile_route(case_id: str):
    from case_engine.goals import intent_profile

    return await intent_profile(case_id)


@router.get("/cases/{case_id}/goal-reconciliation")
async def reconcile_goals_route(case_id: str):
    from case_engine.goals import reconcile_goals

    return await reconcile_goals(case_id)


# ---------------------------------------------------------------------------
# Research mode (STORM-lite)
# ---------------------------------------------------------------------------


@router.get("/cases/{case_id}/perspectives")
async def perspectives_route(case_id: str):
    from case_engine.research import discover_perspectives

    return await discover_perspectives(case_id)


@router.post("/cases/{case_id}/research-questions")
async def research_questions_route(
    case_id: str,
    max_gaps: int = Query(6, ge=1, le=12),
    only_unexplored: bool = Query(True),
):
    from case_engine.research import generate_research_questions

    return await generate_research_questions(
        case_id, max_gaps=max_gaps, only_unexplored=only_unexplored
    )


@router.get("/cases/{case_id}/research-outline")
async def research_outline_route(case_id: str):
    from case_engine.research import research_synthesis_outline

    return await research_synthesis_outline(case_id)


@router.post("/cases/{case_id}/research-brief")
async def research_brief_route(
    case_id: str, max_gaps: int = Query(6, ge=1, le=12)
):
    from case_engine.research import research_brief

    return await research_brief(case_id, max_gaps=max_gaps)
