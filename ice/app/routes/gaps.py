"""app/routes/gaps.py — information gap endpoints (spec Part 4 §13.6).

GET  /cases/{case_id}/gaps          unresolved by default; ?resolved=true
                                    flips to resolved gaps
POST /cases/{case_id}/gaps          create a gap (201)
GET  /gaps/{gap_id}                 fetch one gap
POST /gaps/{gap_id}/resolve         resolve with evidence (idempotent append,
                                    audit gap #27, in the engine)
"""

from fastapi import APIRouter, Depends, Query

from app.deps import GapService, gap_service
from app.schemas import GapCreate, GapResolve, GapResponse

router = APIRouter()


@router.get("/cases/{case_id}/gaps", response_model=list[GapResponse])
async def list_gaps_route(
    case_id: str,
    resolved: bool = Query(False),
    svc: GapService = Depends(gap_service),
):
    items = await svc.list_for_case(case_id, resolved=resolved)
    return [GapResponse.model_validate(g.model_dump()) for g in items]


@router.post("/cases/{case_id}/gaps", response_model=GapResponse, status_code=201)
async def create_gap_route(
    case_id: str,
    payload: GapCreate,
    svc: GapService = Depends(gap_service),
):
    g = await svc.create(
        case_id=case_id,
        needed_evidence=payload.needed_evidence,
        why_needed=payload.why_needed,
        discriminating_hypothesis_ids=payload.discriminating_hypothesis_ids,
        priority=payload.priority,
    )
    return GapResponse.model_validate(g.model_dump())


@router.get("/gaps/{gap_id}", response_model=GapResponse)
async def get_gap_route(gap_id: str, svc: GapService = Depends(gap_service)):
    g = await svc.get(gap_id)
    return GapResponse.model_validate(g.model_dump())


@router.post("/gaps/{gap_id}/resolve", response_model=GapResponse)
async def resolve_gap_route(
    gap_id: str,
    payload: GapResolve,
    svc: GapService = Depends(gap_service),
):
    g = await svc.resolve(gap_id, payload.resolved_by_evidence_id)
    return GapResponse.model_validate(g.model_dump())