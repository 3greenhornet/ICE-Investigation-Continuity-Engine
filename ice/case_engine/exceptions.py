"""case_engine/exceptions.py — typed exceptions for the Investigation Continuity Engine.

Owner: ICE foundation (spec Part 1 §6.11).
Purpose: every "not found" and every infrastructure failure raises a typed
    exception from this module. Public functions in case_engine never return
    ``None`` to signal a missing record (mandatory convention §3.3.6).
Allowed downstream dependencies: none (stdlib only).
"""


class CaseEngineError(Exception):
    """Base class for all Investigation Continuity Engine errors."""


class CaseNotFoundError(CaseEngineError):
    """Raised when a case_id does not exist in the cases table."""


class EvidenceNotFoundError(CaseEngineError):
    """Raised when an evidence_id does not exist in the evidence table."""


class ClaimNotFoundError(CaseEngineError):
    """Raised when a claim_id does not exist in the claims table."""


class HypothesisNotFoundError(CaseEngineError):
    """Raised when a hypothesis_id does not exist in the hypotheses table."""


class ContradictionNotFoundError(CaseEngineError):
    """Raised when a contradiction_id does not exist in the contradictions table."""


class GapNotFoundError(CaseEngineError):
    """Raised when a gap_id does not exist in the information_gaps table."""


class ThreadNotFoundError(CaseEngineError):
    """Raised when a thread_id does not exist in the investigation_threads table."""


class ActionNotFoundError(CaseEngineError):
    """Raised when an action_id does not exist in the actions table."""


class DecisionNotFoundError(CaseEngineError):
    """Raised when a decision_id does not exist in the decisions table."""


class DatabaseUnavailableError(CaseEngineError):
    """Raised when the Postgres pool is None or the database is unreachable."""


class LLMUnavailableError(CaseEngineError):
    """Raised when no LLM provider is configured or the circuit breaker is open."""


class ScopeMismatchError(CaseEngineError):
    """Raised when two statements do not actually conflict — they cover
    different scopes and are both true within their own scopes."""


class TemporalMismatchError(CaseEngineError):
    """Raised when two statements do not actually conflict — they refer to
    different time windows and are both true within their own windows."""