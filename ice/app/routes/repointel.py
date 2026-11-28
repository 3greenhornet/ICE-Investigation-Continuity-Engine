"""app/routes/repointel.py — HTTP surface for RepoIntel repository intelligence.

Owner:      ICE × RepoIntel Integration.
Purpose:    Exposes RepoIntel's precision code tools over FastAPI (/api/v1/repointel):
            - architecture metrics
            - symbol & caller lookups
            - code chunk search
            - goal code maturity verification
"""

from __future__ import annotations

import os
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
def repointel_status():
    """Return RepoIntel connection status and indexed repositories."""
    client = RepoIntelClient()
    return {
        "connected": client.is_available,
        "database_path": client.db_path,
        "repositories": client.list_repositories() if client.is_available else [],
    }


@router.get("/architecture")
def repointel_architecture(repo_id: Optional[str] = Query(None)):
    """Get codebase architectural metrics (files, functions, classes, call edges, hubs)."""
    client = RepoIntelClient()
    return client.get_architecture(repo_id=repo_id)


@router.get("/symbols")
def repointel_symbols(q: str = Query(..., min_length=2), limit: int = Query(10, le=50)):
    """Search AST symbols (functions, classes, methods) matching a query."""
    client = RepoIntelClient()
    symbols = client.search_symbols(query=q, limit=limit)
    return [s.__dict__ for s in symbols]


@router.get("/callers")
def repointel_callers(symbol: str = Query(..., min_length=2)):
    """Find functions that call the given symbol in the call graph."""
    client = RepoIntelClient()
    return client.get_callers(symbol_name=symbol)


@router.get("/code")
def repointel_search_code(q: str = Query(..., min_length=2), limit: int = Query(5, le=25)):
    """Search code implementations and documentation chunks."""
    client = RepoIntelClient()
    return client.search_code(query=q, limit=limit)


@router.get("/impact/{symbol}")
def repointel_impact(
    symbol: str,
    max_depth: int = Query(5, ge=1, le=10),
    limit: int = Query(20, ge=1, le=100),
):
    """Calculate recursive blast radius and upstream caller paths using SQLite Recursive CTE."""
    client = RepoIntelClient()
    return client.get_impact_radius(symbol_name=symbol, max_depth=max_depth, limit=limit)


@router.get("/callees/{symbol}")
def repointel_callees(symbol: str, limit: int = Query(20, ge=1, le=100)):
    """Find functions or classes called downstream by the given symbol."""
    client = RepoIntelClient()
    return client.get_callees(symbol_name=symbol, limit=limit)


@router.get("/gaps")
def repointel_gaps(
    repo_id: Optional[str] = Query(None),
    gap_type: str = Query("all"),
    limit: int = Query(50, ge=1, le=200),
):
    """List codebase gaps (test, doc, incomplete feature, error handling)."""
    client = RepoIntelClient()
    return client.list_gaps(repo_id=repo_id, gap_type=gap_type, limit=limit)


@router.get("/health")
def repointel_health(repo_id: Optional[str] = Query(None)):
    """Calculate aggregate codebase health score (0-100) and letter grade (A-D)."""
    client = RepoIntelClient()
    return client.get_health_score(repo_id=repo_id)


@router.get("/predictions")
def repointel_predictions(
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
def repointel_commits(
    file_path: Optional[str] = Query(None),
    author: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """Get recent Git commits, authors, commit messages, and diff sizes."""
    client = RepoIntelClient()
    return client.get_git_commits(file_path=file_path, author=author, limit=limit)


@router.get("/hotspots")
def repointel_hotspots(limit: int = Query(10, ge=1, le=50)):
    """Get the most frequently changed codebase files (churn hotspots)."""
    client = RepoIntelClient()
    return client.get_git_hotspots(limit=limit)


@router.get("/issues")
def repointel_issues(
    state: Optional[str] = Query(None),
    label: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """Get GitHub issues associated with the repository."""
    client = RepoIntelClient()
    return client.get_issues(state=state, label=label, limit=limit)


@router.get("/pull-requests")
def repointel_prs(
    state: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """Get GitHub pull requests associated with the repository."""
    client = RepoIntelClient()
    return client.get_pull_requests(state=state, limit=limit)


@router.post("/code-maturity")
def repointel_code_maturity(req: CodeMaturityRequest):
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
def repointel_advise(req: AdviseRequest):
    """Advise actionable next steps for in-progress or emerging project goals without false alarms."""
    from companion import DevelopmentCompanion
    comp = DevelopmentCompanion(case_id=req.case_id)
    return comp.advise_next_step(goal_statement=req.statement, clarity_level=req.clarity_level)


@router.get("/findings/{finding_id}/explain")
def repointel_explain_finding(finding_id: str, repo_id: Optional[str] = Query(None)):
    """Retrieve detailed explanation, rule details, severity, and remediation advice for a finding."""
    client = RepoIntelClient()
    return client.explain_finding(finding_id=finding_id, repo_id=repo_id)


@router.get("/file")
def repointel_file(
    path: str = Query(..., description="File path relative to repository root"),
    start: int = Query(1, ge=1, le=10_000_000),
    end: int = Query(100, ge=1, le=10_000_000),
):
    """Read a sanitized excerpt of code from a file indexed by RepoIntel."""
    client = RepoIntelClient()
    return client.get_file_snippet(file_path=path, start_line=start, end_line=end)


class RepoIntelEventPayload(BaseModel):
    event_type: str  # "RepositoryFilesChanged", "FindingDetected", "TestFailed", "CodeDriftDetected"
    repo_id: str = "default"
    file_path: Optional[str] = None
    symbol: Optional[str] = None
    finding_id: Optional[str] = None
    severity: Optional[str] = None
    details: dict = {}
    case_id: Optional[str] = None


@router.post("/event")
async def repointel_event_sync(payload: RepoIntelEventPayload):
    """Event synchronization hook: RepoIntel -> ICE event stream.

    Transforms passive request/response into an event-driven loop:
    - Critical findings auto-flagged for investigation.
    - File changes evaluate goal maturity.
    - Code drift triggers blast radius check.
    """
    client = RepoIntelClient()
    severity = (payload.severity or "").upper() or None
    reaction = {
        "event_received": payload.event_type,
        "repo_id": payload.repo_id,
        "action_taken": "logged",
        "flagged_for_investigation": False,
        # True only when the event was actually written into a case as
        # evidence (previously this was reported True without persisting).
        "investigation_opened": False,
        "persisted_evidence_id": None,
    }

    persist = False
    if payload.event_type == "FindingDetected" and severity in ("HIGH", "CRITICAL"):
        reaction["action_taken"] = "auto_flagged_critical_finding"
        reaction["flagged_for_investigation"] = True
        reaction["advice"] = f"Critical finding {payload.finding_id} flagged for immediate investigation."
        persist = True
    elif payload.event_type == "TestFailed":
        reaction["action_taken"] = "flagged_test_failure"
        reaction["flagged_for_investigation"] = True
        persist = True
    elif payload.event_type == "RepositoryFilesChanged":
        reaction["action_taken"] = "evaluated_code_maturity"
        reaction["file_path"] = payload.file_path
    elif payload.event_type == "CodeDriftDetected" and payload.symbol:
        reaction["action_taken"] = "checked_blast_radius"
        reaction["impact"] = client.get_impact_radius(payload.symbol, max_depth=2) if client.is_available else []

    if persist and payload.case_id:
        # State mutation goes through case_engine ingestion only (AGENTS.md
        # four-plane rule 4); ingest_evidence emits the case_events row.
        from case_engine.case import get_case
        from case_engine.evidence import _create_source, ingest_evidence
        from case_engine.exceptions import CaseNotFoundError

        location = payload.file_path or "?"
        content = (
            f"[RepoIntel {payload.event_type}] severity={severity or 'n/a'} "
            f"finding={payload.finding_id or '-'} symbol={payload.symbol or '-'} "
            f"at {location}\n{payload.details.get('message', '') if isinstance(payload.details, dict) else ''}"
        ).strip()
        try:
            await get_case(payload.case_id)  # raises CaseNotFoundError before any write
            src = await _create_source(
                payload.case_id, "TOOL",
                f"repointel://{payload.repo_id}/{location}",
                metadata={"tool": "repointel", "event_type": payload.event_type},
            )
            ev = await ingest_evidence(
                case_id=payload.case_id,
                content=content,
                source_id=src.source_id,
                evidence_type="TOOL_RESULT",
                metadata={
                    "tool": "repointel",
                    "event_type": payload.event_type,
                    "repo_id": payload.repo_id,
                    "file_path": payload.file_path,
                    "symbol": payload.symbol,
                    "finding_id": payload.finding_id,
                    "severity": severity,
                    "details": payload.details,
                },
            )
            reaction["investigation_opened"] = True
            reaction["persisted_evidence_id"] = ev.evidence_id
            reaction["case_id"] = payload.case_id
            if os.getenv("ICE_EVENT_AUTO_CYCLE", "1").strip().lower() not in ("0", "false", "no"):
                _schedule_bg(_wake_cycle(payload.case_id))
                reaction["cycle_scheduled"] = True
        except CaseNotFoundError:
            # Not retryable: report it instead of a 404 so RepoIntel's
            # outbox does not replay the event forever.
            reaction["error"] = f"case {payload.case_id} not found; event not persisted"
        # DatabaseUnavailableError propagates -> 503, which RepoIntel treats
        # as retryable and keeps in its outbox.
    elif persist:
        reaction["hint"] = "Pass case_id to persist this event as case evidence."
    elif (
        payload.event_type == "RepositoryFilesChanged"
        and payload.case_id
        and os.getenv("ICE_EVENT_AUTO_CYCLE", "1").strip().lower() not in ("0", "false", "no")
    ):
        _schedule_bg(_sync_case_goals(payload.case_id, payload.repo_id))
        reaction["goal_sync_scheduled"] = True

    return reaction


_BG_TASKS: set = set()


def _schedule_bg(coro) -> None:
    import asyncio

    try:
        task = asyncio.get_running_loop().create_task(coro)
        _BG_TASKS.add(task)
        task.add_done_callback(_BG_TASKS.discard)
    except RuntimeError:
        coro.close()


async def _wake_cycle(case_id: str) -> None:
    try:
        from case_engine.cycle import run_investigation_cycle

        await run_investigation_cycle(
            case_id,
            enable_next_best_action=True,
            enable_induction=True,
            enable_workspace=False,
            enable_auto_execute=False,
        )
    except Exception:
        pass


async def _sync_case_goals(case_id: str, repo_id: str) -> None:
    try:
        from case_engine.goals import list_goals, sync_maturity_from_code

        for g in await list_goals(case_id):
            if g.get("status") in ("active", "exploring"):
                await sync_maturity_from_code(g["goal_id"], repo=repo_id)
    except Exception:
        pass


class LabProposalTriggerRequest(BaseModel):
    case_id: str
    instruction: Optional[str] = None
    repo: Optional[str] = None
    files: list[str] = []


@router.post("/lab-proposals")
async def trigger_lab_proposal_route(payload: LabProposalTriggerRequest):
    """Bridge ICE case findings/research gaps to a REVERSIBLE LAB_PROPOSE action."""
    from case_engine.action import research_to_lab_proposal

    return await research_to_lab_proposal(
        case_id=payload.case_id,
        instruction=payload.instruction,
        repo=payload.repo,
        files=payload.files or None,
    )
