"""app/routes/claims.py — claim endpoints (spec Part 4 §13.6).

POST /evidence/{evidence_id}/extract-claims  LLM extraction (201-less POST)
POST /claims                                  create a claim directly
POST /claims/{claim_id}/link                  link evidence (support|contradict)
GET  /cases/{case_id}/claims                  list claims (optionally scoped
                                               to one hypothesis via query)
GET  /claims/{claim_id}                       fetch one claim
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

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