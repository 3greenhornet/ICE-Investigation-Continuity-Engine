"""app/routes/hypotheses.py — hypothesis endpoints (spec Part 4 §13.6).

POST   /cases/{case_id}/hypotheses                create (201)
GET    /cases/{case_id}/hypotheses                list (optional status filter)
GET    /hypotheses/{hypothesis_id}                fetch one
GET    /hypotheses/{hypothesis_id}/versions       version history
POST   /hypotheses/{hypothesis_id}/recompute      recompute confidence; returns
                                                   null when the change is
                                                   below the version threshold
PATCH  /hypotheses/{hypothesis_id}/status         lifecycle status change
GET    /hypotheses/{hypothesis_id}/graph          full evidence subgraph
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.deps import HypothesisService, hypothesis_service
from app.schemas import (
    HypothesisCreate, HypothesisGraph, HypothesisResponse,
    HypothesisStatusUpdate, HypothesisVersionResponse,
)

router = APIRouter()


@router.post(
    "/cases/{case_id}/hypotheses",
    response_model=HypothesisResponse,
    status_code=201,
)
async def create_hypothesis_route(
    case_id: str,
    payload: HypothesisCreate,
    svc: HypothesisService = Depends(hypothesis_service),
):
    h = await svc.create(
        case_id=case_id,
        statement=payload.statement,
        initial_confidence=payload.initial_confidence,
        supporting_claim_ids=payload.supporting_claim_ids,
    )
    return HypothesisResponse.model_validate(h.model_dump())


@router.get("/cases/{case_id}/hypotheses", response_model=list[HypothesisResponse])
async def list_hypotheses_route(
    case_id: str,
    status: Optional[str] = Query(None),
    svc: HypothesisService = Depends(hypothesis_service),
):
    items = await svc.list_for_case(case_id, status=status)
    return [HypothesisResponse.model_validate(h.model_dump()) for h in items]


@router.get("/hypotheses/{hypothesis_id}", response_model=HypothesisResponse)
async def get_hypothesis_route(
    hypothesis_id: str,
    svc: HypothesisService = Depends(hypothesis_service),
):
    h = await svc.get(hypothesis_id)
    return HypothesisResponse.model_validate(h.model_dump())


@router.get(
    "/hypotheses/{hypothesis_id}/versions",
    response_model=list[HypothesisVersionResponse],
)
async def get_versions_route(
    hypothesis_id: str,
    svc: HypothesisService = Depends(hypothesis_service),
):
    versions = await svc.get_versions(hypothesis_id)
    return [HypothesisVersionResponse.model_validate(v.model_dump()) for v in versions]


@router.post(
    "/hypotheses/{hypothesis_id}/recompute",
    response_model=Optional[HypothesisVersionResponse],
)
async def recompute_route(
    hypothesis_id: str,
    svc: HypothesisService = Depends(hypothesis_service),
):
    version = await svc.recompute(hypothesis_id, cause_event_id=f"api_{hypothesis_id}")
    if version is None:
        return None
    return HypothesisVersionResponse.model_validate(version.model_dump())


@router.patch(
    "/hypotheses/{hypothesis_id}/status", response_model=HypothesisResponse
)
async def update_status_route(
    hypothesis_id: str,
    payload: HypothesisStatusUpdate,
    svc: HypothesisService = Depends(hypothesis_service),
):
    h = await svc.set_status(hypothesis_id, payload.status, payload.reason)
    return HypothesisResponse.model_validate(h.model_dump())


@router.get("/hypotheses/{hypothesis_id}/graph", response_model=HypothesisGraph)
async def hypothesis_graph_route(
    hypothesis_id: str,
    svc: HypothesisService = Depends(hypothesis_service),
):
    return HypothesisGraph.model_validate(await svc.get_graph(hypothesis_id))