"""app/routes/evidence.py — evidence endpoints (spec Part 4 §13.6).

POST   /cases/{case_id}/evidence          ingest one item (201); a HUMAN
                                          source is created when source_id
                                          is omitted
POST   /cases/{case_id}/evidence/from-llm  file an external LLM analysis
                                          (201) as MODEL_INFERENCE evidence
                                          with source_llm set (Part 2 §2.4)
POST   /cases/{case_id}/evidence/upload   upload a file (201), parsed into
                                          one or more evidence items
POST   /cases/{case_id}/evidence/url       fetch a URL (201) into evidence
GET    /cases/{case_id}/evidence          active evidence, or stale/superseded
                                          when ?status=stale
GET    /evidence/{evidence_id}            fetch one item
PATCH  /evidence/{evidence_id}/status     change status
POST   /evidence/{evidence_id}/supersede  mark superseded by newer evidence
GET    /evidence/{evidence_id}/chain      provenance chain
"""

import os
import tempfile
from typing import Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile

from app.deps import EvidenceService, evidence_service
from app.schemas import (
    EvidenceCreate, EvidenceFromURL, EvidenceResponse,
    EvidenceStatusUpdate, ExternalAnalysisCreate, ProvenanceChain,
    SupersedeRequest,
)
from case_engine.evidence import _create_source

router = APIRouter()


@router.post(
    "/cases/{case_id}/evidence", response_model=EvidenceResponse, status_code=201
)
async def add_evidence_route(
    case_id: str,
    payload: EvidenceCreate,
    svc: EvidenceService = Depends(evidence_service),
):
    source_id = payload.source_id
    if source_id is None:
        src = await _create_source(case_id, "HUMAN", None)
        source_id = src.source_id
    ev = await svc.ingest(
        case_id=case_id,
        content=payload.content,
        source_id=source_id,
        evidence_type=payload.evidence_type,
        observed_at=payload.observed_at,
        valid_from=payload.valid_from,
        valid_until=payload.valid_until,
        quality_score=payload.quality_score,  # merge spec Part 1 P1/I5
        metadata=payload.metadata,            # merge spec Part 1 P1/I5
    )
    return EvidenceResponse.model_validate(ev.model_dump())


@router.post(
    "/cases/{case_id}/evidence/from-llm",
    response_model=EvidenceResponse,
    status_code=201,
)
async def add_external_analysis_route(
    case_id: str,
    payload: ExternalAnalysisCreate,
    svc: EvidenceService = Depends(evidence_service),
):
    """File an analysis the user obtained from an external LLM (Part 2 §2.4).

    The doc's route body imports ingest_external_analysis inline; we keep
    the deps discipline instead and go through EvidenceService.ingest_external
    (same function object). Validation errors from the engine (empty
    llm_name/prompt/analysis) surface as 400 INVALID_REQUEST via ERROR_MAP.
    """
    ev = await svc.ingest_external(
        case_id=case_id,
        llm_name=payload.llm_name,
        prompt_used=payload.prompt_used,
        analysis_text=payload.analysis_text,
        conversation_context=payload.conversation_context,
        source_uri=payload.source_uri,
    )
    return EvidenceResponse.model_validate(ev.model_dump())


@router.post(
    "/cases/{case_id}/evidence/upload",
    response_model=list[EvidenceResponse],
    status_code=201,
)
async def upload_evidence_route(
    case_id: str,
    file: UploadFile = File(...),
    svc: EvidenceService = Depends(evidence_service),
):
    suffix = os.path.splitext(file.filename or "upload.txt")[1] or ".txt"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        items = await svc.ingest_file(case_id, tmp_path)
    finally:
        os.unlink(tmp_path)
    return [EvidenceResponse.model_validate(e.model_dump()) for e in items]


@router.post(
    "/cases/{case_id}/evidence/url",
    response_model=list[EvidenceResponse],
    status_code=201,
)
async def url_evidence_route(
    case_id: str,
    payload: EvidenceFromURL,
    svc: EvidenceService = Depends(evidence_service),
):
    items = await svc.ingest_url(case_id, payload.url)
    return [EvidenceResponse.model_validate(e.model_dump()) for e in items]


@router.get("/cases/{case_id}/evidence", response_model=list[EvidenceResponse])
async def list_evidence_route(
    case_id: str,
    status: Optional[str] = Query(None),
    svc: EvidenceService = Depends(evidence_service),
):
    if status == "stale":
        items = await svc.get_stale(case_id)
    else:
        items = await svc.get_active(case_id)
    return [EvidenceResponse.model_validate(e.model_dump()) for e in items]


@router.get("/evidence/{evidence_id}", response_model=EvidenceResponse)
async def get_evidence_route(
    evidence_id: str,
    svc: EvidenceService = Depends(evidence_service),
):
    ev = await svc.get(evidence_id)
    return EvidenceResponse.model_validate(ev.model_dump())


@router.patch("/evidence/{evidence_id}/status", response_model=EvidenceResponse)
async def update_evidence_status_route(
    evidence_id: str,
    payload: EvidenceStatusUpdate,
    svc: EvidenceService = Depends(evidence_service),
):
    ev = await svc.update_status(evidence_id, payload.status, payload.reason)
    return EvidenceResponse.model_validate(ev.model_dump())


@router.post("/evidence/{evidence_id}/supersede", response_model=EvidenceResponse)
async def supersede_evidence_route(
    evidence_id: str,
    payload: SupersedeRequest,
    svc: EvidenceService = Depends(evidence_service),
):
    await svc.supersede(evidence_id, payload.new_evidence_id, payload.reason)
    ev = await svc.get(payload.new_evidence_id)
    return EvidenceResponse.model_validate(ev.model_dump())


@router.get("/evidence/{evidence_id}/chain", response_model=ProvenanceChain)
async def evidence_chain_route(
    evidence_id: str,
    svc: EvidenceService = Depends(evidence_service),
):
    chain = await svc.get_chain(evidence_id)
    return ProvenanceChain(evidence_id=evidence_id, chain=chain)