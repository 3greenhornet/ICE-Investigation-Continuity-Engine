"""app/routes/claims.py — claim endpoints (spec Part 4 §13.6).

POST /evidence/{evidence_id}/extract-claims  LLM extraction (201-less POST)
POST /claims                                  create a claim directly
POST /claims/{claim_id}/link                  link evidence (support|contradict)
GET  /cases/{case_id}/claims                  list claims (optionally scoped
                                               to one hypothesis via query)
GET  /claims/{claim_id}                       fetch one claim
POST /claims/{claim_id}/status                retract or supersede a claim (or re-activate);
                                              never deletes: the claim stays in the timeline
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.deps import ClaimService, claim_service
from app.schemas import ClaimCreate, ClaimResponse, LinkEvidenceRequest

router = APIRouter()


@router.post(
    "/evidence/{evidence_id}/extract-claims", response_model=list[ClaimResponse]
)
async def extract_claims_route(
    evidence_id: str,
    svc: ClaimService = Depends(claim_service),
):
    claims = await svc.extract(evidence_id)
    return [ClaimResponse.model_validate(c.model_dump()) for c in claims]


@router.post("/claims", response_model=ClaimResponse, status_code=201)
async def create_claim_route(
    payload: ClaimCreate,
    svc: ClaimService = Depends(claim_service),
):
    c = await svc.create(**payload.model_dump())
    return ClaimResponse.model_validate(c.model_dump())


@router.post("/claims/{claim_id}/link", response_model=ClaimResponse)
async def link_claim_route(
    claim_id: str,
    payload: LinkEvidenceRequest,
    svc: ClaimService = Depends(claim_service),
):
    await svc.link(claim_id, payload.evidence_id, payload.relation)
    c = await svc.get(claim_id)
    return ClaimResponse.model_validate(c.model_dump())


@router.get("/cases/{case_id}/claims", response_model=list[ClaimResponse])
async def list_claims_route(
    case_id: str,
    hypothesis_id: Optional[str] = Query(None),
    svc: ClaimService = Depends(claim_service),
):
    if hypothesis_id:
        claims = await svc.list_for_hypothesis(hypothesis_id)
    else:
        claims = await svc.list_for_case(case_id)
    return [ClaimResponse.model_validate(c.model_dump()) for c in claims]


@router.get("/claims/{claim_id}", response_model=ClaimResponse)
async def get_claim_route(claim_id: str, svc: ClaimService = Depends(claim_service)):
    c = await svc.get(claim_id)
    return ClaimResponse.model_validate(c.model_dump())

class ClaimStatusUpdate(BaseModel):
    status: Literal["ACTIVE", "SUPERSEDED", "RETRACTED"]
    reason: Optional[str] = Field(None, max_length=1000)


@router.post("/claims/{claim_id}/status", response_model=ClaimResponse)
async def claim_status_route(claim_id: str, payload: ClaimStatusUpdate):
    """Lifecycle end for a claim (found by RepoIntel's lifecycle audit: claims
    could be created and listed but never withdrawn). Goes through
    case_engine.claim.set_claim_status, which emits claim_status_changed."""
    from case_engine.claim import set_claim_status

    c = await set_claim_status(claim_id, payload.status, payload.reason)
    return ClaimResponse.model_validate(c.model_dump())
