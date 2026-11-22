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


@router.get("/impact/{symbol}")
async def repointel_impact(
    symbol: str,
    max_depth: int = Query(5, ge=1, le=10),
    limit: int = Query(20, ge=1, le=100),
):
    """Calculate recursive blast radius and upstream caller paths using SQLite Recursive CTE."""
    client = RepoIntelClient()
    return client.get_impact_radius(symbol_name=symbol, max_depth=max_depth, limit=limit)


@router.get("/callees/{symbol}")
async def repointel_callees(symbol: str, limit: int = Query(20, ge=1, le=100)):
    """Find functions or classes called downstream by the given symbol."""
    client = RepoIntelClient()
    return client.get_callees(symbol_name=symbol, limit=limit)


@router.get("/gaps")
async def repointel_gaps(
    repo_id: Optional[str] = Query(None),
    gap_type: str = Query("all"),
    limit: int = Query(50, ge=1, le=200),
):
    """List codebase gaps (test, doc, incomplete feature, error handling)."""
    client = RepoIntelClient()
    return client.list_gaps(repo_id=repo_id, gap_type=gap_type, limit=limit)


@router.get("/health")
async def repointel_health(repo_id: Optional[str] = Query(None)):
    """Calculate aggregate codebase health score (0-100) and letter grade (A-D)."""
    client = RepoIntelClient()
    return client.get_health_score(repo_id=repo_id)


@router.get("/predictions")
async def repointel_predictions(
    file_path: Optional[str] = Query(None),
    prediction_type: Optional[str] = Query(None),
    min_prob: float = Query(0.0, ge=0.0, le=1.0),
    limit: int = Query(25, ge=1, le=100),
):
    """Get ML defect, technical debt, and smell predictions for files."""
    client = RepoIntelClient()
    return client.get_quality_predictions(
        file_path=file_path,
        prediction_type=prediction_type,
        min_probability=min_prob,
        limit=limit,
    )


@router.get("/commits")
async def repointel_commits(
    file_path: Optional[str] = Query(None),
    author: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """Get recent Git commits, authors, commit messages, and diff sizes."""
    client = RepoIntelClient()
    return client.get_git_commits(file_path=file_path, author=author, limit=limit)


@router.get("/hotspots")
async def repointel_hotspots(limit: int = Query(10, ge=1, le=50)):
    """Get the most frequently changed codebase files (churn hotspots)."""
    client = RepoIntelClient()
    return client.get_git_hotspots(limit=limit)


@router.get("/issues")
async def repointel_issues(
    state: Optional[str] = Query(None),
    label: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """Get GitHub issues associated with the repository."""
    client = RepoIntelClient()
    return client.get_issues(state=state, label=label, limit=limit)


@router.get("/pull-requests")
async def repointel_prs(
    state: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """Get GitHub pull requests associated with the repository."""
    client = RepoIntelClient()
    return client.get_pull_requests(state=state, limit=limit)


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


class AdviseRequest(BaseModel):
    statement: str
    clarity_level: int = 2
    case_id: str = "case_default"


@router.post("/advise")
async def repointel_advise(req: AdviseRequest):
    """Advise actionable next steps for in-progress or emerging project goals without false alarms."""
    from companion import DevelopmentCompanion
    comp = DevelopmentCompanion(case_id=req.case_id)
    return comp.advise_next_step(goal_statement=req.statement, clarity_level=req.clarity_level)


@router.get("/findings/{finding_id}/explain")
async def repointel_explain_finding(finding_id: str, repo_id: Optional[str] = Query(None)):
    """Retrieve detailed explanation, rule details, severity, and remediation advice for a finding."""
    client = RepoIntelClient()
    return client.explain_finding(finding_id=finding_id, repo_id=repo_id)


@router.get("/file")
async def repointel_file(
    path: str = Query(..., description="File path relative to repository root"),
    start: int = Query(1, ge=1),
    end: int = Query(100, ge=1),
):
    """Read a sanitized excerpt of code from a file indexed by RepoIntel."""
    client = RepoIntelClient()
    return client.get_file_snippet(file_path=path, start_line=start, end_line=end)
