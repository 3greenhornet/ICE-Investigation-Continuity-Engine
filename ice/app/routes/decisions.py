"""app/routes/decisions.py — decision endpoints (spec Part 4 §13.6).

GET /cases/{case_id}/decisions             50 most recent decisions
                                           (direct SQL per spec)
GET /decisions/{decision_id}/attribution   full decision attribution
                                           (top factors + alternatives)
"""

from fastapi import APIRouter, Depends

from app.deps import DecisionService, decision_service
from app.schemas import DecisionAttribution, DecisionSummary
from case_engine.exceptions import DecisionNotFoundError
from db.connection import get_pool

router = APIRouter()


def _to_summary(r) -> DecisionSummary:
    """Map one decisions row onto the API response model."""
    return DecisionSummary.model_validate({
        "decision_id": r["decision_id"],
        "case_id": r["case_id"],
        "decision_type": r["decision_type"],
        "recommended_action_id": r["recommended_action_id"],
        "rationale": r["rationale"],
        "confidence": r["confidence"],
        "created_at": r["created_at"],
    })


@router.get("/cases/{case_id}/decisions", response_model=list[DecisionSummary])
async def list_decisions_route(case_id: str):
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM decisions WHERE case_id = $1 "
            "ORDER BY created_at DESC LIMIT 50",
            case_id,
        )
    return [_to_summary(r) for r in rows]


@router.get(
    "/decisions/{decision_id}/attribution", response_model=DecisionAttribution
)
async def decision_attribution_route(
    decision_id: str,
    svc: DecisionService = Depends(decision_service),
):
    result = await svc.attribution(decision_id)
    if not result:
        raise DecisionNotFoundError(f"Decision not found: {decision_id}")
    return DecisionAttribution.model_validate(result)