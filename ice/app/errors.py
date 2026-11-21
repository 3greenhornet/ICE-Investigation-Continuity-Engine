"""app/errors.py — HTTP error mapping for the ICE API.

Owner: ICE revamp, spec Part 4 §13.5.
Purpose: map case_engine's typed exceptions onto HTTP status codes and
    machine-readable codes, and register one catch-all exception handler
    on the FastAPI app. Every error body is an app.schemas.ErrorResponse:
    {"error": str, "code": str, "detail": str|null, "remediation": str|null}.

Addition beyond the spec text (improvisation D, worklog ice-0): each error
response carries a ``remediation`` field with a human hint about how to fix
the underlying condition.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from case_engine.exceptions import (
    CaseNotFoundError, EvidenceNotFoundError, ClaimNotFoundError,
    HypothesisNotFoundError, ContradictionNotFoundError, GapNotFoundError,
    ThreadNotFoundError, ActionNotFoundError, DecisionNotFoundError,
    DatabaseUnavailableError, LLMUnavailableError,
    ScopeMismatchError, TemporalMismatchError,
)


ERROR_MAP: dict[type, tuple[int, str]] = {
    CaseNotFoundError: (404, "CASE_NOT_FOUND"),
    EvidenceNotFoundError: (404, "EVIDENCE_NOT_FOUND"),
    ClaimNotFoundError: (404, "CLAIM_NOT_FOUND"),
    HypothesisNotFoundError: (404, "HYPOTHESIS_NOT_FOUND"),
    ContradictionNotFoundError: (404, "CONTRADICTION_NOT_FOUND"),
    GapNotFoundError: (404, "GAP_NOT_FOUND"),
    ThreadNotFoundError: (404, "THREAD_NOT_FOUND"),
    ActionNotFoundError: (404, "ACTION_NOT_FOUND"),
    DecisionNotFoundError: (404, "DECISION_NOT_FOUND"),
    DatabaseUnavailableError: (503, "DATABASE_UNAVAILABLE"),
    LLMUnavailableError: (503, "LLM_UNAVAILABLE"),
    ScopeMismatchError: (400, "SCOPE_MISMATCH"),
    TemporalMismatchError: (400, "TEMPORAL_MISMATCH"),
    ValueError: (400, "INVALID_REQUEST"),
}


# Human remediation hints per error code (improvisation D).
REMEDIATION: dict[str, str] = {
    "CASE_NOT_FOUND": "Verify the case_id exists via GET /api/v1/cases",
    "EVIDENCE_NOT_FOUND": (
        "Verify the id via GET /api/v1/cases/{case_id}/evidence, "
        "or ingest the evidence first"
    ),
    "CLAIM_NOT_FOUND": (
        "Verify the claim_id via GET /api/v1/cases/{case_id}/claims, "
        "or create it via POST /api/v1/claims"
    ),
    "HYPOTHESIS_NOT_FOUND": (
        "Verify the hypothesis_id via GET /api/v1/cases/{case_id}/hypotheses, "
        "or create it via POST /api/v1/cases/{case_id}/hypotheses"
    ),
    "CONTRADICTION_NOT_FOUND": (
        "Verify the contradiction_id via GET /api/v1/cases/{case_id}/contradictions"
    ),
    "GAP_NOT_FOUND": (
        "Verify the gap_id via GET /api/v1/cases/{case_id}/gaps, "
        "or create it via POST /api/v1/cases/{case_id}/gaps"
    ),
    "THREAD_NOT_FOUND": (
        "Verify the thread exists for the case; threads are created by the "
        "investigation cycle, not by a public endpoint"
    ),
    "ACTION_NOT_FOUND": (
        "Verify the action_id via GET /api/v1/cases/{case_id}/actions, "
        "or generate new actions via POST /api/v1/cases/{case_id}/investigate"
    ),
    "DECISION_NOT_FOUND": (
        "Verify the decision_id via GET /api/v1/cases/{case_id}/decisions"
    ),
    "DATABASE_UNAVAILABLE": (
        "Check DATABASE_URL and that Postgres is reachable"
    ),
    "LLM_UNAVAILABLE": (
        "Set at least one provider API key (GEMINI/GROQ/OPENROUTER/MISTRAL)"
    ),
    "SCOPE_MISMATCH": (
        "Narrow the claim scopes so they refer to the same entities, "
        "then retry the comparison"
    ),
    "TEMPORAL_MISMATCH": (
        "Adjust valid_from/valid_until so the claims' time ranges overlap, "
        "then retry the comparison"
    ),
    "INVALID_REQUEST": "Check request field values and types",
    "INTERNAL_ERROR": (
        "Inspect the server logs for the traceback and retry; if the error "
        "persists, report the failing request payload"
    ),
}


def _error_body(exc: Exception, code: str, detail: str) -> dict:
    return {
        "error": str(exc),
        "code": code,
        "detail": detail,
        "remediation": REMEDIATION.get(code),
    }


def _make_handler(status: int, code: str):
    """Build an async handler for one mapped exception class."""

    async def _handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status, content=_error_body(exc, code, None))

    return _handler


def register_exception_handlers(app: FastAPI) -> None:
    """Register the ICE error handlers on ``app``.

    Spec §13.5 registers a single catch-all ``Exception`` handler walking
    ERROR_MAP; we keep that as the fallback for unmapped errors (500
    INTERNAL_ERROR) but ALSO register one handler per mapped exception
    class. Starlette routes class-keyed handlers through
    ExceptionMiddleware, so expected 404/400/503 errors are answered
    without the server-side traceback noise that a bare Exception-keyed
    handler produces (it re-raises after sending the response). Response
    bodies are identical either way.
    """
    for exc_type, (status, code) in ERROR_MAP.items():
        app.add_exception_handler(exc_type, _make_handler(status, code))

    @app.exception_handler(Exception)
    async def _handle(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "code": "INTERNAL_ERROR",
                "detail": str(exc),
                "remediation": REMEDIATION.get("INTERNAL_ERROR"),
            },
        )