"""app/routes/repointel.py — HTTP surface for RepoIntel repository intelligence.

Owner:      ICE × RepoIntel Integration.
Purpose:    Exposes RepoIntel's precision code tools over FastAPI (/api/v1/repointel):
            - architecture metrics
            - symbol & caller lookups
            - code chunk search
            - goal code maturity verification
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from case_engine.repointel_client import RepoIntelClient

router = APIRouter()


class CodeMaturityRequest(BaseModel):
    goal_id: str
    statement: str
    kind: str = "goal"


@router.get("/status")
async def repointel_status():
    """Return RepoIntel connection status and indexed repositories."""
    client = RepoIntelClient()
    return {
        "connected": client.is_available,
        "database_path": client.db_path,
        "repositories": client.list_repositories() if client.is_available else [],
    }


@router.get("/architecture")
async def repointel_architecture(repo_id: Optional[str] = Query(None)):
    """Get codebase architectural metrics (files, functions, classes, call edges, hubs)."""
    client = RepoIntelClient()
    return client.get_architecture(repo_id=repo_id)


@router.get("/symbols")
async def repointel_symbols(q: str = Query(..., min_length=2), limit: int = Query(10, le=50)):
    """Search AST symbols (functions, classes, methods) matching a query."""
    client = RepoIntelClient()
    symbols = client.search_symbols(query=q, limit=limit)
    return [s.__dict__ for s in symbols]


@router.get("/callers")
async def repointel_callers(symbol: str = Query(..., min_length=2)):
    """Find functions that call the given symbol in the call graph."""
    client = RepoIntelClient()
    return client.get_callers(symbol_name=symbol)


@router.get("/code")
async def repointel_search_code(q: str = Query(..., min_length=2), limit: int = Query(5, le=25)):
    """Search code implementations and documentation chunks."""
    client = RepoIntelClient()
    return client.search_code(query=q, limit=limit)


@router.post("/code-maturity")
async def repointel_code_maturity(req: CodeMaturityRequest):
    """Assess whether a goal's candidate code exists, is connected, or tested."""
    client = RepoIntelClient()
    assessment = client.check_goal_code_maturity(
        goal_id=req.goal_id,
        statement=req.statement,
        target_kind=req.kind,
    )
    return assessment.__dict__
