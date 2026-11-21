"""app/routes/replay.py — replay endpoints (spec Part 4 §13.6).

POST /cases/{case_id}/replay/counterfactual   counterfactual influence of one
                                              evidence item on the leading
                                              hypothesis confidence
GET  /cases/{case_id}/events                  case_events ascending, with
                                              optional after_seq cursor +
                                              limit
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.deps import ReplayService, replay_service
from app.schemas import (
    CaseEventResponse, CounterfactualRequest, CounterfactualResult, ReplayRequest,
)
from db.connection import get_pool

router = APIRouter()


@router.post(
    "/cases/{case_id}/replay/counterfactual",
    response_model=CounterfactualResult,
)
async def counterfactual_route(
    case_id: str,
    payload: CounterfactualRequest,
    svc: ReplayService = Depends(replay_service),
):
    result = await svc.counterfactual(case_id, payload.excluded_evidence_id)
    return CounterfactualResult.model_validate(result)


@router.get("/cases/{case_id}/events", response_model=list[CaseEventResponse])
async def events_route(
    case_id: str,
    limit: int = Query(100, ge=1, le=1000),
    after_seq: Optional[int] = Query(None),
):
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        if after_seq is not None:
            rows = await conn.fetch(
                "SELECT * FROM case_events WHERE case_id = $1 AND event_seq > $2 "
                "ORDER BY event_seq ASC LIMIT $3",
                case_id, after_seq, limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM case_events WHERE case_id = $1 "
                "ORDER BY event_seq ASC LIMIT $2",
                case_id, limit,
            )
    return [CaseEventResponse.model_validate(dict(r)) for r in rows]