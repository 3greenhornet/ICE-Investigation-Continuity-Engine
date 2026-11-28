"""app/routes/discovery.py — HTTP surface for cross-domain mechanism discovery.

    POST /discovery                                  run discovery for a problem
                                                     (cards -> case evidence when
                                                     case_id is given, and the
                                                     mechanism_lab catalogue)
    GET  /discovery/fields                           field posterior (what the
                                                     bandit has learned)
    POST /discovery/mechanisms/{mechanism_id}/decision
                                                     reuse | adapt | reject |
                                                     defer | revisit; reuse/adapt
                                                     /reject train the field bandit

Projection plane: calls case_engine.discovery, never writes tables itself.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()


class DiscoveryRequest(BaseModel):
    problem: str = Field(..., min_length=8, max_length=4000)
    case_id: Optional[str] = None
    n_fields: int = Field(5, ge=1, le=8)
    ingest: bool = True
    catalogue: bool = True
    repo: Optional[str] = Field(None, description="RepoIntel repo name/id for the 'already in the repo?' check")


class MechanismDecision(BaseModel):
    decision: str = Field(..., pattern="^(reuse|adapt|reject|defer|revisit|pending)$")
    note: str = Field("", max_length=1000)


@router.post("/discovery")
async def discovery_route(payload: DiscoveryRequest):
    """Abstract the problem, pick diverse fields (OpenAlex census + LLM,
    Thompson x DPP), extract source-cited mechanism cards, attach GitHub
    implementations and RepoIntel matches. Takes about 1-2 minutes."""
    from case_engine.discovery import discover

    if payload.case_id:
        from case_engine.case import get_case

        await get_case(payload.case_id)  # 404 before any network call or write
    return await discover(payload.problem, case_id=payload.case_id, n_fields=payload.n_fields,
                          ingest=payload.ingest, catalogue=payload.catalogue, repo=payload.repo)


@router.get("/discovery/fields")
async def discovery_fields_route():
    from case_engine.discovery import FieldBandit

    return {"fields": FieldBandit().stats()}


@router.post("/discovery/mechanisms/{mechanism_id}/decision")
async def discovery_decision_route(mechanism_id: str, payload: MechanismDecision):
    from case_engine.discovery import decide_mechanism

    try:
        return await decide_mechanism(mechanism_id, payload.decision, payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
