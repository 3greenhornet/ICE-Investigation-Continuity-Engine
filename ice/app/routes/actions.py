"""app/routes/actions.py — action endpoints (spec Part 4 §13.6).

GET   /cases/{case_id}/actions         proposed (pending) actions
GET   /actions/{action_id}             fetch one action
POST  /actions/{action_id}/approve     proposed -> approved
POST  /actions/{action_id}/execute     approved -> executing -> completed|failed
POST  /actions/{action_id}/reject      proposed -> rejected (with reason)
PATCH /actions/{action_id}             edit a proposed action — AUDIT FIX #10
                                       ([Accept][Reject][Edit] loop) via
                                       action.edit_action
"""

from fastapi import APIRouter, Depends, Query

from app.deps import ActionService, action_service
from app.schemas import ActionEditRequest, ActionResponse, RejectRequest

router = APIRouter()


@router.get("/cases/{case_id}/actions", response_model=list[ActionResponse])
async def list_actions_route(
    case_id: str,
    status: str = Query("proposed"),
    svc: ActionService = Depends(action_service),
):
    if status == "proposed":
        items = await svc.get_pending(case_id)
    else:
        # Future: filter by other statuses
        items = await svc.get_pending(case_id)
    return [ActionResponse.model_validate(a.model_dump()) for a in items]


@router.get("/actions/{action_id}", response_model=ActionResponse)
async def get_action_route(
    action_id: str,
    svc: ActionService = Depends(action_service),
):
    a = await svc.get(action_id)
    return ActionResponse.model_validate(a.model_dump())


@router.post("/actions/{action_id}/approve", response_model=ActionResponse)
async def approve_action_route(
    action_id: str,
    svc: ActionService = Depends(action_service),
):
    a = await svc.approve(action_id)
    return ActionResponse.model_validate(a.model_dump())


@router.post("/actions/{action_id}/execute", response_model=ActionResponse)
async def execute_action_route(
    action_id: str,
    svc: ActionService = Depends(action_service),
):
    a = await svc.execute(action_id)
    return ActionResponse.model_validate(a.model_dump())


@router.post("/actions/{action_id}/reject", response_model=ActionResponse)
async def reject_action_route(
    action_id: str,
    payload: RejectRequest,
    svc: ActionService = Depends(action_service),
):
    a = await svc.reject(action_id, payload.reason)
    return ActionResponse.model_validate(a.model_dump())


@router.patch("/actions/{action_id}", response_model=ActionResponse)
async def edit_action_route(
    action_id: str,
    payload: ActionEditRequest,
    svc: ActionService = Depends(action_service),
):
    a = await svc.edit(
        action_id,
        target=payload.target,
        expected_outcome=payload.expected_outcome,
        cost_estimate=payload.cost_estimate,
        risk=payload.risk,
    )
    return ActionResponse.model_validate(a.model_dump())