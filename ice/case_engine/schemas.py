"""case_engine/schemas.py — canonical Pydantic v2 domain contracts for ICE.

Owner: ICE foundation (spec Part 1 §6.1–§6.9).
Purpose: single canonical import location for every ICE domain model and its
    Literal type aliases. Other case_engine modules, the FastAPI layer and the
    CLI import their models from here (``from case_engine.schemas import ...``)
    so that field names, types, defaults and constraints match the database
    schema in db/migrations/012_case_model.sql exactly.
Allowed downstream dependencies: pydantic only (no DB, no LLM, no case_engine
    siblings — this module is at the bottom of the import graph).

Audit-mandated addition (worklog ice-0, gap #1/#19): the Evidence model gains
``metadata: dict = Field(default_factory=dict)`` so ingest_evidence can persist
its metadata argument (the DB column is added in migration 012).
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

# =====================================================================
# §6.1 Case
# =====================================================================

CaseStatus = Literal[
    "open", "investigating", "deciding", "resolved", "abandoned"
]

# Part 2 (fix-2-a, doc §1.3): what "done" means for a case — cases.case_type
# (migration 015) selects the decide()/generate_actions() branch.
#   debug    — confidence-threshold-based resolution (existing behavior)
#   research — synthesis completeness (no contradictions + no high-priority gaps)
#   explore  — manual resolution (user marks the case resolved)
CaseType = Literal["debug", "research", "explore"]


class Case(BaseModel):
    case_id: str
    title: str
    objective: str
    decision_deadline: Optional[datetime] = None
    required_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    cost_budget_tool_calls: int = Field(default=100, ge=1)
    cost_budget_tokens: int = Field(default=500000, ge=1000)
    status: CaseStatus = "open"
    case_type: CaseType = "debug"          # NEW (Part 2 §1.3)
    created_at: datetime
    updated_at: datetime


# =====================================================================
# §6.2 Evidence / Source
# =====================================================================

EvidenceType = Literal[
    "OBSERVATION", "MEASUREMENT", "DOCUMENT_CLAIM", "HUMAN_ASSERTION",
    "TOOL_RESULT", "EXPERIMENT_RESULT", "MODEL_INFERENCE", "MODEL_SUMMARY",
]

EvidenceStatus = Literal[
    "ACTIVE", "STALE", "SUPERSEDED", "DISPUTED", "RESOLVED", "ARCHIVED"
]

SourceType = Literal[
    "DOCUMENT", "TOOL", "HUMAN", "URL", "DATABASE", "LOG_FILE", "CODE_REPO"
]


class Evidence(BaseModel):
    evidence_id: str
    case_id: str
    source_id: str
    evidence_type: EvidenceType
    content: str
    embedding: Optional[list[float]] = None
    observed_at: Optional[datetime] = None
    recorded_at: datetime
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None
    source_lineage_id: Optional[str] = None
    duplicate_cluster_id: Optional[str] = None
    quality_score: float = Field(default=0.5, ge=0.0, le=1.0)
    status: EvidenceStatus = "ACTIVE"
    # audit gap #1/#19 fix: ingest_evidence accepts metadata; persist it
    metadata: dict = Field(default_factory=dict)
    extracted_claim_ids: list[str] = Field(default_factory=list)
    # NEW (Part 2 §1.3): set only by ingest_external_analysis() for
    # MODEL_INFERENCE evidence that came from an external LLM; None for
    # every other evidence type and for ICE's own internal inferences.
    # _evidence_weight credits external analyses 0.4 while zeroing ICE's
    # own model output (self-confirmation guard).
    source_llm: Optional[str] = None


class Source(BaseModel):
    source_id: str
    case_id: str
    source_type: SourceType
    source_uri: Optional[str] = None
    source_hash: Optional[str] = None
    parent_source_id: Optional[str] = None
    lineage_id: Optional[str] = None
    duplicate_cluster_id: Optional[str] = None
    authority_score: float = Field(default=0.5, ge=0.0, le=1.0)
    directness_score: float = Field(default=0.5, ge=0.0, le=1.0)
    freshness_score: float = Field(default=0.5, ge=0.0, le=1.0)
    independence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime


# =====================================================================
# §6.3 Claim
# =====================================================================

ClaimType = Literal[
    "MEASUREMENT", "OBSERVATION", "CORRELATION", "TEMPORAL_ASSOCIATION",
    "CAUSAL_CLAIM", "INTERPRETATION", "PREDICTION", "ASSUMPTION",
]


ClaimStatus = Literal["ACTIVE", "SUPERSEDED", "RETRACTED"]


class Claim(BaseModel):
    claim_id: str
    case_id: str
    statement: str
    claim_type: ClaimType
    status: ClaimStatus = "ACTIVE"
    scope: Optional[dict] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: datetime
    updated_at: datetime


# =====================================================================
# §6.4 Hypothesis / HypothesisVersion
# =====================================================================

HypothesisStatus = Literal[
    "PROPOSED", "SUPPORTED", "CHALLENGED", "SURVIVED",
    "FALSIFIED", "CONFIRMED", "REMAINS_OPEN",
]

# NEW (Part 2 §1.3): where a hypothesis came from.
#   user            — proposed via API/CLI
#   external_llm    — extracted from an external LLM analysis
#   ice_generated   — auto-proposed by an ICE cycle
#   source_material — extracted from a document/source
HypothesisOrigin = Literal[
    "user", "external_llm", "ice_generated", "source_material"
]


class Hypothesis(BaseModel):
    hypothesis_id: str
    case_id: str
    statement: str
    normalized_statement: str
    status: HypothesisStatus = "PROPOSED"
    current_version_id: Optional[str] = None
    created_at: datetime
    origin: HypothesisOrigin = "user"                 # NEW (Part 2 §1.3)
    origin_detail: Optional[str] = None               # NEW (Part 2 §1.3)


class HypothesisVersion(BaseModel):
    version_id: str
    hypothesis_id: str
    version_number: int = Field(ge=1)
    confidence: float = Field(ge=0.0, le=1.0)
    prior_confidence: Optional[float] = None
    supporting_claim_ids: list[str] = Field(default_factory=list)
    contradicting_claim_ids: list[str] = Field(default_factory=list)
    supporting_evidence_added: list[str] = Field(default_factory=list)
    supporting_evidence_removed: list[str] = Field(default_factory=list)
    contradicting_evidence_added: list[str] = Field(default_factory=list)
    reason: str
    cause_event_id: str
    created_at: datetime


# =====================================================================
# §6.5 Contradiction
# =====================================================================

RelationType = Literal[
    "contradiction", "refinement", "scope_mismatch", "temporal_mismatch",
    "independent",
]

ContradictionStatus = Literal["active", "resolving", "resolved", "archived"]


class Contradiction(BaseModel):
    contradiction_id: str
    case_id: str
    claim_a: str
    claim_b: str
    scope_a: Optional[dict] = None
    scope_b: Optional[dict] = None
    time_a: Optional[datetime] = None
    time_b: Optional[datetime] = None
    relation_type: RelationType = "contradiction"
    severity: float = Field(default=0.5, ge=0.0, le=1.0)
    source_evidence_a: Optional[str] = None
    source_evidence_b: Optional[str] = None
    status: ContradictionStatus = "active"
    resolution_summary: Optional[str] = None
    resolution_evidence_id: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None


# =====================================================================
# §6.6 InformationGap
# =====================================================================


class InformationGap(BaseModel):
    gap_id: str
    case_id: str
    needed_evidence: str
    current_state: Optional[str] = None
    why_needed: Optional[str] = None
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    discriminating_hypothesis_ids: list[str] = Field(default_factory=list)
    depends_on_gap_ids: list[str] = Field(default_factory=list)
    blocks_action_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    resolved_by_evidence_ids: list[str] = Field(default_factory=list)
    resolved_at: Optional[datetime] = None


# =====================================================================
# §6.7 InvestigationThread
# =====================================================================


class InvestigationThread(BaseModel):
    thread_id: str
    case_id: str
    question: str
    origin: str
    created_at: datetime
    last_progress_at: datetime
    strength: float = Field(default=0.5, ge=0.0, le=1.0)
    decision_impact: float = Field(default=0.5, ge=0.0, le=1.0)
    uncertainty: float = Field(default=0.5, ge=0.0, le=1.0)
    blocking_question_id: Optional[str] = None
    reinforcement_history: list[int] = Field(default_factory=list)
    is_active: bool = True


# =====================================================================
# §6.8 Action
# =====================================================================

ActionType = Literal[
    "SEARCH", "RETRIEVE", "COMPARE", "INSPECT", "RUN_TEST",
    "ASK_HUMAN", "REVISIT_HYPOTHESIS", "FIND_COUNTEREVIDENCE",
    "WAIT", "ABSTAIN",
]

ApprovalClass = Literal[
    "READ_ONLY", "REVERSIBLE", "IRREVERSIBLE", "EXTERNAL_SIDE_EFFECT"
]

ActionStatus = Literal[
    "proposed", "approved", "executing", "completed", "failed", "cancelled",
    "rejected",
]


class Action(BaseModel):
    action_id: str
    case_id: str
    action_type: ActionType
    target: Optional[str] = None
    expected_outcome: Optional[str] = None
    cost_estimate: float = Field(default=0.5, ge=0.0)
    risk: float = Field(default=0.0, ge=0.0, le=1.0)
    expected_information_gain: float = Field(default=0.5, ge=0.0, le=1.0)
    decision_impact: float = Field(default=0.5, ge=0.0, le=1.0)
    falsification_value: float = Field(default=0.0, ge=0.0, le=1.0)
    approval_required: bool = False
    approval_class: ApprovalClass = "READ_ONLY"
    status: ActionStatus = "proposed"
    result_evidence_ids: list[str] = Field(default_factory=list)
    # Hari ICE improvements pass (migration 018): structured execution
    # outcome + payload. outcome is None until the action executes; the
    # vocabulary is EVIDENCE_PRODUCED / INFORMATIVE_NO_EVIDENCE /
    # UNINFORMATIVE / UNSUPPORTED (see case_engine.action).
    outcome: Optional[str] = None
    result_payload: dict = Field(default_factory=dict)
    created_at: datetime
    completed_at: Optional[datetime] = None


# =====================================================================
# §6.9 Decision
# =====================================================================

DecisionType = Literal["ACT", "INVESTIGATE", "ABSTAIN"]


class Decision(BaseModel):
    decision_id: str
    case_id: str
    decision_type: DecisionType
    recommended_action_id: Optional[str] = None
    rationale: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    alternatives_considered: list[dict] = Field(default_factory=list)
    top_factors: dict = Field(default_factory=dict)
    created_at: datetime