"""app/routes/cases.py — case endpoints (spec Part 4 §13.6).

POST   /cases                    create a case (201); case_type selects
                                   debug/research/explore behaviour and
                                   the required_confidence default
                                   (Part 2 §3.4)
GET    /cases                    list cases (status + case_type + limit
                                   filters)
GET    /cases/{case_id}          fetch one case
PATCH  /cases/{case_id}          change case status
GET    /cases/{case_id}/summary  counts + leading hypothesis + next action
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.deps import CaseService, case_service
from app.schemas import (
    CaseCreate, CaseResponse, CaseSummary, CaseUpdate,
)

router = APIRouter()


@router.post("/cases", response_model=CaseResponse, status_code=201)
async def create_case_route(
    payload: CaseCreate,
    svc: CaseService = Depends(case_service),
):
    # Part 2 §3.4: enumerate the kwargs instead of **payload.model_dump()
    # — required_confidence is now Optional so the engine can default it
    # per case_type (0.85 debug / 0.0 research & explore), and case_type
    # rides along explicitly.
    case = await svc.create(
        title=payload.title,
        objective=payload.objective,
        decision_deadline=payload.decision_deadline,
        required_confidence=payload.required_confidence,
        cost_budget_tool_calls=payload.cost_budget_tool_calls,
        cost_budget_tokens=payload.cost_budget_tokens,
        case_type=payload.case_type,
    )
    return CaseResponse.model_validate(case.model_dump())


@router.get("/cases", response_model=list[CaseResponse])
async def list_cases_route(
    status: Optional[str] = Query(None),
    case_type: Optional[str] = Query(None),  # Part 2 §3.4 filter
    limit: int = Query(50, ge=1, le=1000),
    svc: CaseService = Depends(case_service),
):
    cases = await svc.list(status=status, limit=limit, case_type=case_type)
    return [CaseResponse.model_validate(c.model_dump()) for c in cases]


@router.get("/cases/{case_id}", response_model=CaseResponse)
async def get_case_route(case_id: str, svc: CaseService = Depends(case_service)):
    case = await svc.get(case_id)
    return CaseResponse.model_validate(case.model_dump())


@router.patch("/cases/{case_id}", response_model=CaseResponse)
async def update_case_route(
    case_id: str,
    payload: CaseUpdate,
    svc: CaseService = Depends(case_service),
):
    case = await svc.update_status(case_id, payload.status, payload.reason)
    return CaseResponse.model_validate(case.model_dump())


@router.get("/cases/{case_id}/summary", response_model=CaseSummary)
async def case_summary_route(case_id: str, svc: CaseService = Depends(case_service)):
    return CaseSummary.model_validate(await svc.summary(case_id))