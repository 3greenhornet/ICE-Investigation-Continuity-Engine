"""app/schemas.py — request/response Pydantic models for the ICE API.

Owner: ICE revamp, spec Part 4 §13.3 (complete).
Purpose: every request and response model used by app/routes/. Nothing else
    exists in this file. Domain models live in case_engine/schemas.py; the
    models here are the HTTP wire contracts.

Additions beyond the spec text (mandated by the worklog ice-0 plan):
    * ErrorResponse.remediation — human hint per error code (improvisation D).
    * ActionEditRequest — body for PATCH /actions/{action_id} (audit gap #10).
    * CaseMetrics / ConsistencyReport — responses for the metrics and
      consistency endpoints (improvisations B and C).
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ============ CASES ============

class CaseCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    objective: str = Field(..., min_length=1, max_length=2000)
    decision_deadline: Optional[datetime] = None
    # Part 2 §3.4: Optional so the engine can default per case_type —
    # create_case fills 0.85 for "debug" and 0.0 for "research"/"explore"
    # when the caller leaves it unset (fix-2-a wired the engine side).
    required_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    cost_budget_tool_calls: int = Field(100, ge=1)
    cost_budget_tokens: int = Field(500000, ge=1000)
    case_type: Literal["debug", "research", "explore"] = "debug"  # Part 2 §3.4


class CaseResponse(BaseModel):
    case_id: str
    title: str
    objective: str
    decision_deadline: Optional[datetime]
    required_confidence: float
    cost_budget_tool_calls: int
    cost_budget_tokens: int
    case_type: str = "debug"  # Part 2 §3.4 (default tolerates pre-015 rows)
    status: str
    created_at: datetime
    updated_at: datetime


class CaseUpdate(BaseModel):
    status: Literal["open", "investigating", "deciding", "resolved", "abandoned"]
    reason: Optional[str] = None


class CaseSummaryCounts(BaseModel):
    evidence_active: int
    evidence_total: int
    claims: int
    hypotheses: int
    hypotheses_supported: int
    contradictions_active: int
    gaps_unresolved: int
    threads_active: int
    actions_proposed: int
    decisions: int
    events: int


class LeadingHypothesisSummary(BaseModel):
    hypothesis_id: str
    statement: str
    confidence: float
    version_number: int


class NextActionSummary(BaseModel):
    action_id: str
    action_type: str
    target: Optional[str]
    priority: float


class CaseSummary(BaseModel):
    case_id: str
    title: str
    objective: str
    case_type: str = "debug"  # Part 2 §3.4 (get_case_summary now returns it)
    status: str
    required_confidence: float
    decision_deadline: Optional[datetime]
    counts: CaseSummaryCounts
    leading_hypothesis: Optional[LeadingHypothesisSummary]
    next_action: Optional[NextActionSummary]


# ============ EVIDENCE ============

class EvidenceCreate(BaseModel):
    content: str = Field(..., min_length=1)
    source_id: Optional[str] = None
    evidence_type: Optional[Literal[
        "OBSERVATION", "MEASUREMENT", "DOCUMENT_CLAIM", "HUMAN_ASSERTION",
        "TOOL_RESULT", "EXPERIMENT_RESULT", "MODEL_INFERENCE",
        "MODEL_SUMMARY",
    ]] = None
    observed_at: Optional[datetime] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    # Merge spec Part 1 §5.1 P1 / improvements I1+I5: caller-supplied quality
    # override (None -> engine's max(source_quality, type_default)) and
    # ingest-time metadata (threaded through to ingest_evidence).
    quality_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    metadata: Optional[dict] = None
    # RepoIntel integration (CHANGES.md "Cross-project integration hardening"):
    # optional source attribution so tool-produced evidence (e.g. RepoIntel
    # SAST findings) is not recorded as a HUMAN source. Ignored when
    # source_id is given. Default None -> "HUMAN" (unchanged behaviour).
    source_type: Optional[Literal[
        "DOCUMENT", "TOOL", "HUMAN", "URL", "DATABASE", "LOG_FILE", "CODE_REPO",
    ]] = None
    source_uri: Optional[str] = Field(None, max_length=2000)


class EvidenceFromURL(BaseModel):
    url: str = Field(..., pattern=r"^https?://")


class ExternalAnalysisCreate(BaseModel):
    """Body for POST /cases/{case_id}/evidence/from-llm (Part 2 §2.4).

    Records an analysis the user obtained from an external LLM (Claude,
    GPT, ...) as MODEL_INFERENCE evidence with source_llm set, so
    _evidence_weight credits it 0.4 while ICE's own inferences stay 0.0.
    """

    llm_name: str = Field(..., min_length=1, max_length=100)
    prompt_used: str = Field(..., min_length=1)
    analysis_text: str = Field(..., min_length=1)
    conversation_context: Optional[str] = None
    source_uri: Optional[str] = None


class EvidenceStatusUpdate(BaseModel):
    status: Literal["ACTIVE", "STALE", "SUPERSEDED", "DISPUTED", "RESOLVED",
                    "ARCHIVED"]
    reason: str = Field(..., min_length=1)


class SupersedeRequest(BaseModel):
    new_evidence_id: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)


class EvidenceResponse(BaseModel):
    evidence_id: str
    case_id: str
    source_id: str
    evidence_type: str
    content: str
    observed_at: Optional[datetime]
    recorded_at: datetime
    valid_from: Optional[datetime]
    valid_until: Optional[datetime]
    supersedes: Optional[str]
    superseded_by: Optional[str]
    source_lineage_id: Optional[str]
    duplicate_cluster_id: Optional[str]
    quality_score: float
    status: str
    extracted_claim_ids: list[str]
    # Merge spec Part 1 §5.5 P5: close the metadata asymmetry — it was
    # write-only (EvidenceCreate accepts it, the response dropped it).
    # source_llm arrives with migration 015 in Part 2 (§2.4): populated by
    # ingest_external_analysis, read back on every evidence response.
    metadata: dict = Field(default_factory=dict)
    source_llm: Optional[str] = None


class ProvenanceChainEntry(BaseModel):
    node_id: str
    node_type: str
    relation: str
    metadata: dict


class ProvenanceChain(BaseModel):
    evidence_id: str
    chain: list[ProvenanceChainEntry]


# ============ CLAIMS ============

class ClaimCreate(BaseModel):
    case_id: str
    statement: str = Field(..., min_length=1)
    claim_type: Literal[
        "MEASUREMENT", "OBSERVATION", "CORRELATION", "TEMPORAL_ASSOCIATION",
        "CAUSAL_CLAIM", "INTERPRETATION", "PREDICTION", "ASSUMPTION",
    ]
    scope: Optional[dict] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    supporting_evidence_ids: Optional[list[str]] = None


class LinkEvidenceRequest(BaseModel):
    evidence_id: str
    relation: Literal["support", "contradict"]


class ClaimResponse(BaseModel):
    claim_id: str
    case_id: str
    statement: str
    claim_type: str
    status: Literal["ACTIVE", "SUPERSEDED", "RETRACTED"] = "ACTIVE"
    scope: Optional[dict]
    valid_from: Optional[datetime]
    valid_until: Optional[datetime]
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]
    confidence: float
    created_at: datetime
    updated_at: datetime


# ============ HYPOTHESES ============

class HypothesisCreate(BaseModel):
    statement: str = Field(..., min_length=1)
    initial_confidence: float = Field(0.5, ge=0.0, le=1.0)
    supporting_claim_ids: Optional[list[str]] = None


class HypothesisStatusUpdate(BaseModel):
    status: Literal[
        "PROPOSED", "SUPPORTED", "CHALLENGED",
        "SURVIVED", "FALSIFIED", "CONFIRMED", "REMAINS_OPEN",
    ]
    reason: str = Field(..., min_length=1)


class HypothesisResponse(BaseModel):
    hypothesis_id: str
    case_id: str
    statement: str
    normalized_statement: str
    status: str
    current_version_id: Optional[str]
    # Part 2 §11.3: origin tracking (migration 015) so the hypothesis
    # board can badge 👤 you / 🤖 external LLM / 🔧 ICE / 📄 source.
    origin: str = "user"
    origin_detail: Optional[str] = None
    created_at: datetime


class HypothesisVersionResponse(BaseModel):
    version_id: str
    hypothesis_id: str
    version_number: int
    confidence: float
    prior_confidence: Optional[float]
    supporting_claim_ids: list[str]
    contradicting_claim_ids: list[str]
    supporting_evidence_added: list[str]
    supporting_evidence_removed: list[str]
    contradicting_evidence_added: list[str]
    reason: str
    cause_event_id: str
    created_at: datetime


class HypothesisGraphEvidence(BaseModel):
    evidence_id: str
    content_preview: str
    evidence_type: str
    source_id: str
    status: str


class HypothesisGraphClaim(BaseModel):
    claim_id: str
    statement: str
    claim_type: str
    confidence: float
    evidence: list[HypothesisGraphEvidence]


class HypothesisGraphVersion(BaseModel):
    version_number: int
    confidence: float
    prior_confidence: Optional[float]
    reason: str
    created_at: datetime


class HypothesisGraph(BaseModel):
    hypothesis_id: str
    statement: str
    status: str
    current_confidence: float
    supporting_claims: list[HypothesisGraphClaim]
    contradicting_claims: list[HypothesisGraphClaim]
    version_history: list[HypothesisGraphVersion]


# ============ CONTRADICTIONS ============

class DetectRequest(BaseModel):
    claim_a_id: str
    claim_b_id: str
    confidence_threshold: float = Field(0.7, ge=0.0, le=1.0)


class ResolveRequest(BaseModel):
    resolution_evidence_id: str
    summary: str = Field(..., min_length=1)


class ContradictionResponse(BaseModel):
    contradiction_id: str
    case_id: str
    claim_a: str
    claim_b: str
    scope_a: Optional[dict]
    scope_b: Optional[dict]
    time_a: Optional[datetime]
    time_b: Optional[datetime]
    relation_type: str
    severity: float
    status: str
    resolution_summary: Optional[str]
    resolution_evidence_id: Optional[str]
    created_at: datetime
    resolved_at: Optional[datetime]


# ============ GAPS ============

class GapCreate(BaseModel):
    needed_evidence: str = Field(..., min_length=1)
    why_needed: str = ""
    discriminating_hypothesis_ids: list[str] = Field(default_factory=list)
    priority: float = Field(0.5, ge=0.0, le=1.0)


class GapResolve(BaseModel):
    resolved_by_evidence_id: str


class GapResponse(BaseModel):
    gap_id: str
    case_id: str
    needed_evidence: str
    current_state: Optional[str]
    why_needed: Optional[str]
    priority: float
    discriminating_hypothesis_ids: list[str]
    depends_on_gap_ids: list[str]
    blocks_action_ids: list[str]
    created_at: datetime
    resolved_by_evidence_ids: list[str]
    resolved_at: Optional[datetime]


# ============ ACTIONS ============

class ActionResponse(BaseModel):
    action_id: str
    case_id: str
    action_type: str
    target: Optional[str]
    expected_outcome: Optional[str]
    cost_estimate: float
    risk: float
    expected_information_gain: float
    decision_impact: float
    falsification_value: float
    approval_required: bool
    approval_class: str
    status: str
    result_evidence_ids: list[str]
    created_at: datetime
    completed_at: Optional[datetime]


class RejectRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class ActionEditRequest(BaseModel):
    """Partial edit of a proposed action (audit gap #10 — [Accept][Reject][Edit]).

    Only fields explicitly provided are updated; None means "leave unchanged".
    """

    target: Optional[str] = None
    expected_outcome: Optional[str] = None
    cost_estimate: Optional[float] = Field(None, ge=0.0)
    risk: Optional[float] = Field(None, ge=0.0, le=1.0)


# ============ DECISIONS ============

class DecisionSummary(BaseModel):
    decision_id: str
    case_id: str
    decision_type: str
    recommended_action_id: Optional[str]
    rationale: str
    confidence: float
    created_at: datetime


class DecisionAttribution(BaseModel):
    decision_id: str
    case_id: str
    decision_type: str
    rationale: str
    confidence: float
    top_factors: dict
    alternatives: list[dict]
    recommended_action_id: Optional[str]


# ============ INVESTIGATION ============

class EndogenousSuggestion(BaseModel):
    """One endogenous "the system noticed" candidate (Part 2 §5.2).

    Shape of collect_case_endogenous_candidates' dicts (the engine's extra
    internal_activation key is intentionally dropped here — it is a
    substrate-facing field, not part of the wire contract).
    """

    id: str
    content: str
    item_type: str
    source: str
    urgency: float
    origin: str
    activated_by: str


class InvestigateRequest(BaseModel):
    force_recompute: bool = False
    enable_falsification: bool = False
    enable_next_best_action: bool = True
    # Part 2 §4.4: approve + execute the top-priority action inside the
    # cycle (auto_executed_action in the response records what ran).
    enable_auto_execute: bool = False
    # The former action-count cap field was removed (merge spec Part 1
    # §6.1 S1): run_investigation_cycle never honored the parameter — the
    # field silently did nothing.


class HypothesisChange(BaseModel):
    hypothesis_id: str
    statement: str
    prior_confidence: Optional[float]
    current_confidence: float
    version_number: int
    cause_event_id: str


class InvestigationResult(BaseModel):
    case_id: str
    leading_hypothesis: Optional[LeadingHypothesisSummary]
    hypothesis_changes: list[HypothesisChange]
    new_contradictions: list[str]
    resolved_contradictions: list[str]
    new_gaps: list[str]
    new_actions: list[str]
    recommended_action: Optional[NextActionSummary]
    decision: DecisionSummary
    events_emitted: int
    latency_seconds: float
    # Part 2 §7.2/§4.4: lifecycle transitions applied this cycle and the
    # action auto-approved + executed when enable_auto_execute was set.
    lifecycle_changes: list[dict] = Field(default_factory=list)
    auto_executed_action: Optional[dict] = None


# ============ REPLAY ============

class ReplayRequest(BaseModel):
    up_to_event_seq: Optional[int] = None


class CounterfactualRequest(BaseModel):
    excluded_evidence_id: str


class CounterfactualResult(BaseModel):
    evidence_id: str
    original_confidence: float
    counterfactual_confidence: float
    causal_influence: float
    interpretation: Literal["decision_critical", "moderate", "negligible"]


class CaseEventResponse(BaseModel):
    event_id: str
    case_id: str
    event_type: str
    event_seq: int
    conversation_turn: Optional[int]
    wall_clock_time: datetime
    payload: dict


# ============ METRICS & CONSISTENCY (improvisations B + C) ============

class CaseMetrics(BaseModel):
    """Aggregated runtime metrics (case_engine.metrics.get_case_metrics)."""

    case_id: str
    evidence_trace_coverage: float = 0.0
    investigation_efficiency: float = 0.0
    evidence_utilization: float = 0.0
    decision_counts_by_type: dict[str, int] = Field(default_factory=dict)
    actions_by_status: dict[str, int] = Field(default_factory=dict)


class ConsistencyReport(BaseModel):
    """Result of case_engine.consistency.validate_case — a list of violations."""

    violations: list[str] = Field(default_factory=list)


# ============ HEALTH ============

class HealthResponse(BaseModel):
    status: str
    db: Optional[str] = None
    llm: Optional[str] = None


# ============ ERRORS ============

class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    code: str
    remediation: Optional[str] = None