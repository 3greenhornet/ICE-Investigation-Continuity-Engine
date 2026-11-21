"""app/routes/contradictions.py — contradiction endpoints (spec Part 4 §13.6).

GET  /cases/{case_id}/contradictions           active contradictions
POST /contradictions/detect                     run the detection pipeline;
                                                returns null body when the two
                                                claims are NOT a contradiction
POST /contradictions/{id}/resolve               resolve with evidence
GET  /contradictions/{contradiction_id}         fetch one — GAP FIX (audit
                                                #43) via get_contradiction
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.deps import ContradictionService, contradiction_service
from app.schemas import ContradictionResponse, DetectRequest, ResolveRequest

router = APIRouter()


@router.get(
    "/cases/{case_id}/contradictions",
    response_model=list[ContradictionResponse],
)
async def list_contradictions_route(
    case_id: str,
    status: Optional[str] = Query("active"),
    svc: ContradictionService = Depends(contradiction_service),
):
    items = await svc.get_active(case_id)
    return [ContradictionResponse.model_validate(c.model_dump()) for c in items]


@router.post(
    "/contradictions/detect", response_model=Optional[ContradictionResponse]
)
async def detect_contradiction_route(
    payload: DetectRequest,
    svc: ContradictionService = Depends(contradiction_service),
):
    c = await svc.detect(
        payload.claim_a_id, payload.claim_b_id, payload.confidence_threshold
    )
    if c is None:
        return None
    return ContradictionResponse.model_validate(c.model_dump())


@router.get(
    "/contradictions/{contradiction_id}", response_model=ContradictionResponse
)
async def get_contradiction_route(
    contradiction_id: str,
    svc: ContradictionService = Depends(contradiction_service),
):
    c = await svc.get(contradiction_id)
    return ContradictionResponse.model_validate(c.model_dump())


@router.post(
    "/contradictions/{contradiction_id}/resolve",
    response_model=ContradictionResponse,
)
async def resolve_contradiction_route(
    contradiction_id: str,
    payload: ResolveRequest,
    svc: ContradictionService = Depends(contradiction_service),
):
    c = await svc.resolve(
        contradiction_id, payload.resolution_evidence_id, payload.summary
    )
    return ContradictionResponse.model_validate(c.model_dump())