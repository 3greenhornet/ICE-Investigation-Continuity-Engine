"""app/routes/health.py — health endpoints (spec Part 4 §13.6).

GET /health        liveness — process is up
GET /health/ready  readiness — db (pool initialized) + llm (at least one
                   active fallback model) status
"""

from fastapi import APIRouter

from app.schemas import HealthResponse
from case_engine.llm import ACTIVE_FALLBACK_MODELS
from db.connection import get_pool

router = APIRouter()


@router.get("/health", response_model=HealthResponse, response_model_exclude_none=True)
async def liveness():
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=HealthResponse)
async def readiness():
    pool = await get_pool()
    db_ok = pool is not None
    llm_ok = len(ACTIVE_FALLBACK_MODELS) > 0
    return HealthResponse(
        status="ok" if (db_ok and llm_ok) else "degraded",
        db="ok" if db_ok else "unavailable",
        llm="ok" if llm_ok else "unavailable",
    )