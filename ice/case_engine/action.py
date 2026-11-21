"""case_engine/action.py — proposed/Approved/executed action management.

Owner:      ICE revamp, Part 3 §6 (plus audit gap fixes #3, #4, #10 and
            improvisation G — ActionExecutor protocol).
Purpose:    Create, propose, edit, approve, reject, and execute investigation
            actions; generate candidate actions from the current case state
            (information gaps, falsification candidates, contradictions,
            stagnation).
Deps:       db.connection, case_engine (_emit_event), case_engine.exceptions,
            case_engine.schemas, case_engine.case, case_engine.hypothesis,
            case_engine.information_gap, case_engine.contradiction,
            case_engine.evidence (lazily, via executors). The falsification
            module is imported lazily inside generate_actions to avoid a
            circular import (falsification imports create_action from here).

Conventions: Part 2 §0 + Part 3 §0. async + typed; case_id first on mutators;
one _emit_event per mutation; UTC datetimes; asyncpg $1 style; explicit row
mapping via _row_to_action.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional, Protocol

from db.connection import get_pool
from case_engine import _emit_event
from case_engine.exceptions import (
    ActionNotFoundError,
    DatabaseUnavailableError,
)
from case_engine.schemas import Action, ActionType, ApprovalClass, ActionStatus
from case_engine.case import get_case
from case_engine.information_gap import list_gaps_for_case, compute_gap_priority, create_gap
from case_engine.contradiction import get_active_contradictions
from case_engine.hypothesis import list_hypotheses_for_case, get_current_version

logger = logging.getLogger(__name__)

ENABLE_FALSIFICATION_LOOP = os.getenv("ENABLE_FALSIFICATION_LOOP", "false").lower() == "true"

SOURCE_FAMILIES = [
    "DOCUMENT", "URL", "TOOL", "HUMAN", "DATABASE", "LOG_FILE", "CODE_REPO",
]


def _row_to_action(row) -> Action:
    """Explicit row -> Action mapping (no **row splat; Part 2 §0 rule 8)."""
    return Action(
        action_id=row["action_id"],
        case_id=row["case_id"],
        action_type=row["action_type"],
        target=row["target"],
        expected_outcome=row["expected_outcome"],
        cost_estimate=row["cost_estimate"],
        risk=row["risk"],
        expected_information_gain=row["expected_information_gain"],
        decision_impact=row["decision_impact"],
        falsification_value=row["falsification_value"],
        approval_required=row["approval_required"],
        approval_class=row["approval_class"],
        status=row["status"],
        result_evidence_ids=list(row["result_evidence_ids"] or []),
        # Hari ICE improvements pass (migration 018): map defensively so a
        # database that has not applied 018 still loads actions (None/{})
        # instead of crashing — same pattern as hypothesis.py's origin.
        outcome=(
            row["outcome"] if "outcome" in row.keys() else None
        ),
        result_payload=(
            _parse_payload(row["result_payload"])
            if "result_payload" in row.keys() else {}
        ),
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


def _parse_payload(raw) -> dict:
    """Defensively decode the result_payload jsonb column."""
    import json as _json

    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = _json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _validate_action_floats(
    cost_estimate: float,
    risk: float,
    expected_information_gain: float,
    decision_impact: float,
    falsification_value: float,
) -> None:
    """Shared validation for create_action / edit_action.

    Raises:
        ValueError on invalid float ranges.
    """
    if not (0.0 <= risk <= 1.0):
        raise ValueError("risk must be in [0, 1]")
    if cost_estimate < 0:
        raise ValueError("cost_estimate must be >= 0")
    for name, v in [
        ("expected_information_gain", expected_information_gain),
        ("decision_impact", decision_impact),
        ("falsification_value", falsification_value),
    ]:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"{name} must be in [0, 1]")


async def create_action(
    case_id: str,
    action_type: ActionType,
    target: Optional[str],
    expected_outcome: Optional[str],
    cost_estimate: float = 0.5,
    risk: float = 0.0,
    expected_information_gain: float = 0.5,
    decision_impact: float = 0.5,
    falsification_value: float = 0.0,
    approval_required: bool = False,
    approval_class: ApprovalClass = "READ_ONLY",
) -> Action:
    """Create a proposed action.

    Postconditions:
        actions row created with status='proposed'.
        action_proposed event emitted.
    Raises: ValueError on invalid float ranges; DatabaseUnavailableError.
    Side effects:
        INSERT INTO actions (1).
        _emit_event("action_proposed", {...}).
    """
    _validate_action_floats(
        cost_estimate=cost_estimate,
        risk=risk,
        expected_information_gain=expected_information_gain,
        decision_impact=decision_impact,
        falsification_value=falsification_value,
    )

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot create action: no DB pool")

    action_id = f"act_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO actions (
                action_id, case_id, action_type, target, expected_outcome,
                cost_estimate, risk,
                expected_information_gain, decision_impact, falsification_value,
                approval_required, approval_class, status, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'proposed', $13)
            """,
            action_id, case_id, action_type, target, expected_outcome,
            cost_estimate, risk,
            expected_information_gain, decision_impact, falsification_value,
            approval_required, approval_class, now,
        )

    await _emit_event(case_id, "action_proposed", {
        "action_id": action_id,
        "action_type": action_type,
        "target": target,
    })

    return await get_action(action_id)


async def _is_stagnant(case_id: str, window: int = 5) -> bool:
    """Return True if the last `window` acted-upon actions share one action_type.

    "Acted-upon" = status IN ('proposed', 'approved', 'executing',
    'completed'). Merge spec Part 1 §3 R6: 'proposed' is now included —
    with only post-approval statuses, stagnation could never fire on a
    fresh case (every action is still 'proposed' inside a single
    investigation cycle), so the page-80/81 "stagnation -> switch retrieval
    strategy" behavior only triggered after five human-approved turns.
    Defined BEFORE generate_actions (audit gap #3: the spec's forward
    reference was a reordering landmine).
    Raises: nothing — returns False when the pool is unavailable.
    """
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT action_type FROM actions
            WHERE case_id = $1
              AND status IN ('proposed', 'approved', 'executing', 'completed')
            ORDER BY created_at DESC
            LIMIT $2
            """,
            case_id, window,
        )
    if len(rows) < window:
        return False
    types = {row["action_type"] for row in rows}
    return len(types) == 1


async def _find_unexplored_source_families(case_id: str) -> list[str]:
    """Return source_type families not yet represented in ingested evidence.

    Defined BEFORE generate_actions (audit gap #3).
    Raises: nothing — returns [] when the pool is unavailable.
    """
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT source_type FROM sources WHERE case_id = $1", case_id,
        )
    seen = {row["source_type"] for row in rows}
    return [f for f in SOURCE_FAMILIES if f not in seen]


async def generate_actions(case_id: str) -> list[Action]:
    """Generate candidate actions. Branches on case.case_type (Part 2 §3.3).

    - 'debug': _generate_actions_debug — budget enforcement, gaps,
      falsification, contradictions, stagnation (existing behavior).
    - 'research': _generate_actions_research — SEARCH for high-priority
      gaps, COMPARE for contradictions, REVISIT_HYPOTHESIS for uncited
      evidence; no stagnation detection.
    - 'explore': _generate_actions_explore — debug actions plus
      endogenous surfacing.

    HONEST NOTE (doc Part 2 §12 shortcut #12): only the research and
    explore branches consume endogenous candidates. In debug mode the
    "system noticed" suggestions are informational only — deliberate,
    because debug mode assumes the user is actively driving the
    investigation while research/explore assume ambient exploration.

    Raises:
        CaseNotFoundError: if the case does not exist.
        DatabaseUnavailableError.
    """
    # NOTE (Phase-0 correctness fix): get_case is imported at module level
    # since the Hari-ICE improvements pass — the old function-local import
    # inside generate_actions left _generate_actions_debug's `get_case`
    # reference unbound, so EVERY debug-mode action generation raised
    # NameError (invisible to the skipped integration suite; caught by
    # actually running tests/integration against Postgres).
    case = await get_case(case_id)

    if case.case_type == "research":
        return await _generate_actions_research(case)
    if case.case_type == "explore":
        return await _generate_actions_explore(case)
    return await _generate_actions_debug(case_id)


async def _generate_actions_debug(case_id: str) -> list[Action]:
    """Debug-mode action generation — the existing sources, unchanged.

    Budget enforcement (audit gap #4): the case is loaded via
    case_engine.case.get_case and the existing action count is compared to
    case.cost_budget_tool_calls. If the count has reached (or exceeded) the
    budget, a warning is logged and [] is returned — a case can no longer
    spawn unbounded actions.

    Sources (evaluated in order, each capped):
        1. Top 5 unresolved information gaps with recomputed priority >= 0.3
           -> RETRIEVE.
        2. Falsification candidates for the top-confidence hypothesis
           (if ENABLE_FALSIFICATION_LOOP) -> FIND_COUNTEREVIDENCE.
        3. Top 3 active contradictions -> COMPARE.
        4. Stagnation detection (if last 5 acted-upon actions share one
           action_type) -> up to 2 SEARCH actions in unexplored source
           families.

    Postconditions:
        Returns a list of Action (all status='proposed'). May be empty.
    """
    # --- Budget enforcement (audit gap #4) -------------------------------
    case = await get_case(case_id)
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot generate actions: no DB pool")

    async with pool.acquire() as conn:
        existing_count = await conn.fetchval(
            "SELECT COUNT(*) FROM actions WHERE case_id = $1", case_id,
        )
    if existing_count >= case.cost_budget_tool_calls:
        logger.warning(
            "Budget exhausted for case %s: %d actions >= cost_budget_tool_calls=%d",
            case_id, existing_count, case.cost_budget_tool_calls,
        )
        return []

    actions: list[Action] = []

    # Source 1 — information gaps
    try:
        gaps = await list_gaps_for_case(case_id, resolved=False)
    except Exception as exc:
        logger.warning("Gap lookup failed: %s", exc)
        gaps = []

    for gap in gaps[:5]:
        try:
            priority = await compute_gap_priority(gap.gap_id)
        except Exception:
            priority = gap.priority
        if priority < 0.3:
            continue
        actions.append(await create_action(
            case_id=case_id,
            action_type="RETRIEVE",
            target=gap.needed_evidence,
            expected_outcome=gap.why_needed or "",
            cost_estimate=0.3,
            risk=0.0,
            expected_information_gain=priority,
            decision_impact=0.5,
            approval_required=False,
            approval_class="READ_ONLY",
        ))

    # Source 2 — falsification
    if ENABLE_FALSIFICATION_LOOP:
        try:
            # Imported lazily: case_engine.falsification imports create_action
            # from this module, so a module-level import would be circular.
            from case_engine.falsification import run_falsification_loop
            hypotheses = await list_hypotheses_for_case(case_id)
            top = None
            top_conf = -1.0
            for h in hypotheses:
                try:
                    v = await get_current_version(h.hypothesis_id)
                except Exception:
                    continue
                if v.confidence > top_conf:
                    top_conf = v.confidence
                    top = h
            if top is not None:
                falsification_actions = await run_falsification_loop(top.hypothesis_id)
                actions.extend(falsification_actions)
        except Exception as exc:
            logger.warning("Falsification loop failed: %s", exc)

    # Source 3 — contradictions
    try:
        contradictions = await get_active_contradictions(case_id)
    except Exception:
        contradictions = []

    for c in contradictions[:3]:
        actions.append(await create_action(
            case_id=case_id,
            action_type="COMPARE",
            target=f"{c.claim_a} vs {c.claim_b}",
            expected_outcome="Determine which claim is supported by evidence",
            cost_estimate=0.4,
            risk=0.0,
            expected_information_gain=c.severity,
            decision_impact=0.6,
            approval_required=False,
            approval_class="READ_ONLY",
        ))

    # Source 4 — stagnation
    try:
        if await _is_stagnant(case_id):
            unexplored = await _find_unexplored_source_families(case_id)
            for family in unexplored[:2]:
                actions.append(await create_action(
                    case_id=case_id,
                    action_type="SEARCH",
                    target=f"Search {family} for new evidence",
                    expected_outcome="Diversify evidence sources",
                    cost_estimate=0.5,
                    risk=0.0,
                    expected_information_gain=0.4,
                    decision_impact=0.4,
                    approval_required=False,
                    approval_class="READ_ONLY",
                ))
    except Exception as exc:
        logger.debug("Stagnation detection failed: %s", exc)

    return actions


async def _generate_actions_research(case) -> list[Action]:
    """Research-mode action generation (Part 2 §3.3, Feature 2B).

    Priorities:
    1. SEARCH actions for high-priority gaps
    2. COMPARE actions for contradictions
    3. REVISIT_HYPOTHESIS actions for uncited evidence (via endogenous)
    No stagnation detection — research mode assumes continuous material
    intake.

    HONEST NOTE (doc Part 2 §12 shortcut #4): the "top 5 gaps, top 3
    contradictions, top 3 endogenous" caps are arbitrary round numbers.
    The principled version would bound by the case's token budget or the
    user's attention capacity, not by a count. Deferred.
    """
    actions: list[Action] = []
    case_id = case.case_id

    # Budget check
    pool = await get_pool()
    if pool:
        async with pool.acquire() as conn:
            existing_count = await conn.fetchval(
                "SELECT COUNT(*) FROM actions WHERE case_id = $1", case_id,
            )
        if existing_count >= case.cost_budget_tool_calls:
            logger.warning(
                "Budget exhausted for research case %s: %d actions >= %d",
                case_id, existing_count, case.cost_budget_tool_calls,
            )
            return []

    # Source 1 — high-priority gaps
    gaps = await list_gaps_for_case(case_id, resolved=False)
    for gap in gaps[:5]:
        try:
            priority = await compute_gap_priority(gap.gap_id)
        except Exception:
            priority = gap.priority
        if priority < 0.4:
            continue
        actions.append(await create_action(
            case_id=case_id,
            action_type="SEARCH",
            target=gap.needed_evidence,
            expected_outcome=gap.why_needed or "Fill research gap",
            cost_estimate=0.3,
            risk=0.0,
            expected_information_gain=priority,
            decision_impact=0.5,
            approval_required=False,
            approval_class="READ_ONLY",
        ))

    # Source 2 — contradictions to reconcile
    contradictions = await get_active_contradictions(case_id)
    for c in contradictions[:3]:
        actions.append(await create_action(
            case_id=case_id,
            action_type="COMPARE",
            target=f"{c.claim_a} vs {c.claim_b}",
            expected_outcome="Reconcile the two sources",
            cost_estimate=0.4,
            risk=0.0,
            expected_information_gain=c.severity,
            decision_impact=0.6,
            approval_required=False,
            approval_class="READ_ONLY",
        ))

    # Source 3 — uncited evidence (endogenous; Part 2 §5). Imported
    # lazily: endogenous imports investigation_thread, and keeping the
    # import inside the branch keeps module load light.
    try:
        from case_engine.endogenous import collect_case_endogenous_candidates
        endogenous = await collect_case_endogenous_candidates(case_id, current_turn=0)
    except Exception as exc:
        logger.debug("Endogenous collection failed for research case %s: %s", case_id, exc)
        endogenous = []
    for cand in endogenous[:3]:
        actions.append(await create_action(
            case_id=case_id,
            action_type="REVISIT_HYPOTHESIS",
            target=cand["content"],
            expected_outcome="Integrate previously-unused material",
            cost_estimate=0.2,
            risk=0.0,
            expected_information_gain=min(1.0, float(cand["urgency"])),
            decision_impact=0.4,
            approval_required=False,
            approval_class="READ_ONLY",
        ))

    return actions


async def _generate_actions_explore(case) -> list[Action]:
    """Explore-mode action generation (Part 2 §3.3, Feature 2B).

    Debug actions + endogenous surfacing (the "system noticed you haven't
    looked at this" material becomes REVISIT_HYPOTHESIS actions).
    """
    actions = await _generate_actions_debug(case.case_id)

    # Endogenous candidates — lazy import, same rationale as research mode.
    try:
        from case_engine.endogenous import collect_case_endogenous_candidates
        endogenous = await collect_case_endogenous_candidates(case.case_id, current_turn=0)
    except Exception as exc:
        logger.debug("Endogenous collection failed for explore case %s: %s", case.case_id, exc)
        endogenous = []
    for cand in endogenous[:3]:
        actions.append(await create_action(
            case_id=case.case_id,
            action_type="REVISIT_HYPOTHESIS",
            target=cand["content"],
            expected_outcome=cand["content"][:100],
            cost_estimate=0.2,
            risk=0.0,
            expected_information_gain=min(1.0, float(cand["urgency"])),
            decision_impact=0.3,
            approval_required=False,
            approval_class="READ_ONLY",
        ))

    return actions


async def get_action(action_id: str) -> Action:
    """Load an action by ID.

    Raises:
        ActionNotFoundError: if no row exists.
        DatabaseUnavailableError: if the DB pool is None.
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot load action: no DB pool")
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM actions WHERE action_id = $1", action_id)
    if row is None:
        raise ActionNotFoundError(f"Action not found: {action_id}")
    return _row_to_action(row)


async def get_pending_actions(case_id: str) -> list[Action]:
    """Return the case's proposed actions, newest first.

    Raises: DatabaseUnavailableError.
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot list actions: no DB pool")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM actions WHERE case_id = $1 AND status = 'proposed' ORDER BY created_at DESC",
            case_id,
        )
    return [_row_to_action(row) for row in rows]


async def edit_action(
    action_id: str,
    target: Optional[str] = None,
    expected_outcome: Optional[str] = None,
    cost_estimate: Optional[float] = None,
    risk: Optional[float] = None,
) -> Action:
    """Edit a proposed action (audit gap #10 — the [Accept][Reject][Edit] loop).

    Only fields explicitly provided (not None) are updated; the rest are left
    untouched. Provided floats are validated exactly like create_action
    (risk in [0, 1], cost_estimate >= 0).

    No event is emitted: the Part 1 §7 event table has no event type for
    action edits, and "silent event types are forbidden" — so edits are
    deliberately event-less until a schema change adds one. (Documented here
    per the audit's request.)

    Preconditions: action exists and status == 'proposed'.
    Postconditions: provided fields updated; action reloaded and returned.
    Raises:
        ActionNotFoundError, DatabaseUnavailableError.
        ValueError: if status is not 'proposed' or floats are invalid.
    Side effects: UPDATE INTO actions (1). No event.
    """
    action = await get_action(action_id)
    if action.status != "proposed":
        raise ValueError(
            f"Action {action_id} can only be edited in 'proposed' state (current: {action.status})"
        )

    # Validate provided floats with the same rules as create_action.
    _validate_action_floats(
        cost_estimate=cost_estimate if cost_estimate is not None else action.cost_estimate,
        risk=risk if risk is not None else action.risk,
        expected_information_gain=action.expected_information_gain,
        decision_impact=action.decision_impact,
        falsification_value=action.falsification_value,
    )

    updates: list[str] = []
    params: list = []
    if target is not None:
        params.append(target)
        updates.append(f"target = ${len(params)}")
    if expected_outcome is not None:
        params.append(expected_outcome)
        updates.append(f"expected_outcome = ${len(params)}")
    if cost_estimate is not None:
        params.append(cost_estimate)
        updates.append(f"cost_estimate = ${len(params)}")
    if risk is not None:
        params.append(risk)
        updates.append(f"risk = ${len(params)}")

    if updates:
        pool = await get_pool()
        if pool is None:
            raise DatabaseUnavailableError("Cannot edit action: no DB pool")
        params.append(action_id)
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE actions SET {', '.join(updates)} WHERE action_id = ${len(params)}",
                *params,
            )

    return await get_action(action_id)


async def approve_action(action_id: str) -> Action:
    """Transition status proposed -> approved. Emits action_approved."""
    action = await get_action(action_id)
    if action.status != "proposed":
        raise ValueError(f"Action {action_id} is not in 'proposed' state (current: {action.status})")

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot approve: no DB pool")
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE actions SET status = 'approved' WHERE action_id = $1", action_id,
        )

    await _emit_event(action.case_id, "action_approved", {
        "action_id": action_id,
        "approved_by": "human",
    })
    return await get_action(action_id)


async def reject_action(action_id: str, reason: str) -> Action:
    """Transition status proposed -> rejected. No approval event.

    The Part 1 §7 event table defines no `action_rejected` event type, so —
    per "silent event types are forbidden" — this mutation deliberately emits
    nothing. The `reason` is recorded in the application log (the actions
    table has no reason column).
    """
    action = await get_action(action_id)
    if action.status != "proposed":
        raise ValueError(f"Action {action_id} is not in 'proposed' state")
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot reject: no DB pool")
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE actions SET status = 'rejected', completed_at = $1 WHERE action_id = $2",
            datetime.now(timezone.utc), action_id,
        )
    logger.info("Action %s rejected: %s", action_id, reason)
    return await get_action(action_id)


class ActionExecutor(Protocol):
    """Structured action boundary (improvisation G, extended this pass).

    An executor turns an approved action into a structured ExecutionResult
    (evidence ids + outcome + diagnostics). The outcome vocabulary is the
    P1 fix from the Hari ICE improvements document: an action is no longer
    "successfully completed merely because an executor returned an empty
    list" — activity can no longer masquerade as progress.
    """

    async def execute(self, action: Action) -> "ExecutionResult": ...


# ---------------------------------------------------------------------------
# Execution outcomes (P1: "give every action type a contract, an actual
# executor, an expected result schema, and a clear failure state")
# ---------------------------------------------------------------------------

OUTCOME_EVIDENCE_PRODUCED = "EVIDENCE_PRODUCED"
OUTCOME_INFORMATIVE_NO_EVIDENCE = "INFORMATIVE_NO_EVIDENCE"
OUTCOME_UNINFORMATIVE = "UNINFORMATIVE"
OUTCOME_UNSUPPORTED = "UNSUPPORTED"

# Action types whose CONTRACT requires new evidence to count as progress.
_EVIDENCE_EXPECTING_TYPES = {
    "SEARCH", "RETRIEVE", "INSPECT", "RUN_TEST", "COMPARE",
    "FIND_COUNTEREVIDENCE",
}


class ExecutionResult:
    """Structured executor result: what executing an action actually produced.

    outcome:
        EVIDENCE_PRODUCED       — new evidence rows exist (result_evidence_ids)
        INFORMATIVE_NO_EVIDENCE — no new evidence, but a real, useful result
                                  (a retrieval ranking, a survived
                                  falsification search, a scheduled wake-up)
        UNINFORMATIVE           — ran, learned nothing (empty retrieval,
                                  no-op semantics)
        UNSUPPORTED             — no real executor exists for this action
                                  instance; the old NullExecutor behavior,
                                  now VISIBLE instead of silent
    """

    def __init__(
        self,
        outcome: str,
        evidence_ids: Optional[list[str]] = None,
        notes: str = "",
        diagnostics: Optional[dict] = None,
    ):
        self.outcome = outcome
        self.evidence_ids = list(evidence_ids or [])
        self.notes = notes
        self.diagnostics = diagnostics or {}

    def to_payload(self) -> dict:
        return {
            "outcome": self.outcome,
            "notes": self.notes,
            "diagnostics": self.diagnostics,
        }

    @classmethod
    def empty_list(cls, action: "Action") -> "ExecutionResult":
        """The old `return []` semantics, made honest."""
        return cls(
            OUTCOME_UNSUPPORTED,
            notes=(
                "No real executor for this action instance "
                f"({action.action_type}); previously this silently counted "
                "as a completed action."
            ),
        )


class NullExecutor:
    """Explicit unsupported-executor marker (closed-world benchmark runs may
    still inject it deliberately). Now reports UNSUPPORTED instead of a
    fake success."""

    async def execute(self, action: Action) -> ExecutionResult:
        return ExecutionResult.empty_list(action)


class WebSearchExecutor:
    """SEARCH executor: ingest the target URL as evidence.

    Only acts when the action is a SEARCH whose target is an http(s) URL
    (human-edited targets qualify; generator-produced targets like
    "Search LOG_FILE for new evidence" are UNSUPPORTED, logged as a warning
    per Part 2 §4.3 — previously these returned [] and still counted as
    "completed"). The scrape backend is configured by the SCRAPER env var
    inside ingest_evidence_from_url (scrapfly | firecrawl). A missing
    scraper API key raises RuntimeError (Part 2 §4.3) — the failure
    propagates to execute_action's action_failed path.
    """

    async def execute(self, action: Action) -> ExecutionResult:
        if action.action_type != "SEARCH" or not action.target:
            return ExecutionResult.empty_list(action)
        url = action.target.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            logger.warning(
                "SEARCH action %s has non-URL target '%s' — no real search "
                "executor for natural-language targets (marking UNSUPPORTED)",
                action.action_id, action.target,
            )
            return ExecutionResult(
                OUTCOME_UNSUPPORTED,
                notes=(
                    f"Natural-language search target '{action.target[:80]}' "
                    "has no search backend wired; edit the action target to "
                    "an http(s) URL or configure a search provider."
                ),
            )

        scraper = os.getenv("SCRAPER", "scrapfly").lower()
        if scraper == "scrapfly" and not os.getenv("SCRAPFLY_API_KEY"):
            raise RuntimeError(
                "SCRAPFLY_API_KEY is not set — cannot execute SEARCH action. "
                "Set SCRAPER=firecrawl and FIRECRAWL_API_KEY, or install "
                "scrapfly-sdk and set SCRAPFLY_API_KEY."
            )
        if scraper == "firecrawl" and not os.getenv("FIRECRAWL_API_KEY"):
            raise RuntimeError(
                "FIRECRAWL_API_KEY is not set — cannot execute SEARCH action."
            )

        from case_engine.evidence import ingest_evidence_from_url
        items = await ingest_evidence_from_url(action.case_id, url)
        if not items:
            return ExecutionResult(
                OUTCOME_UNINFORMATIVE,
                notes="Scrape returned no ingestable content",
            )
        return ExecutionResult(
            OUTCOME_EVIDENCE_PRODUCED,
            evidence_ids=[e.evidence_id for e in items],
            notes=f"Ingested {len(items)} evidence item(s) from URL",
        )


class FileInspectExecutor:
    """INSPECT executor: ingest an existing file path as evidence.

    Only acts when the action is an INSPECT whose target is an existing
    filesystem path. Non-existent paths are UNSUPPORTED (previously a
    silent [] success).
    """

    async def execute(self, action: Action) -> ExecutionResult:
        if action.action_type != "INSPECT" or not action.target:
            return ExecutionResult.empty_list(action)
        path = action.target.strip()
        if not os.path.isfile(path):
            return ExecutionResult(
                OUTCOME_UNSUPPORTED,
                notes=f"Target path does not exist: {path}",
            )
        from case_engine.evidence import ingest_evidence_from_file
        items = await ingest_evidence_from_file(action.case_id, path, source_type="DOCUMENT")
        if not items:
            return ExecutionResult(
                OUTCOME_UNINFORMATIVE,
                notes=f"File parsed but produced no evidence items: {path}",
            )
        return ExecutionResult(
            OUTCOME_EVIDENCE_PRODUCED,
            evidence_ids=[e.evidence_id for e in items],
            notes=f"Ingested {len(items)} evidence item(s) from file",
        )


class HumanAskExecutor:
    """ASK_HUMAN executor: record the question as an information gap.

    The gap's needed_evidence is the action target; the gap creation itself
    emits information_gap_created. No evidence yet — a human must answer —
    so the outcome is INFORMATIVE_NO_EVIDENCE (the question is now
    officially pending, which is real state).
    """

    async def execute(self, action: Action) -> ExecutionResult:
        if action.action_type != "ASK_HUMAN" or not action.target:
            return ExecutionResult.empty_list(action)
        gap = await create_gap(
            case_id=action.case_id,
            needed_evidence=action.target,
            why_needed=f"Human question asked by action {action.action_id}",
            discriminating_hypothesis_ids=[],
        )
        return ExecutionResult(
            OUTCOME_INFORMATIVE_NO_EVIDENCE,
            notes=f"Question recorded as information gap {gap.gap_id}",
            diagnostics={"gap_id": gap.gap_id},
        )


class RetrieveExecutor:
    """RETRIEVE executor: hybrid search over the case's own evidence.

    The foundation the second-pass audit said was missing ("the RETRIEVE
    action has no proper foundation"). Runs case_engine.retrieval's hybrid
    search (vector + FTS + RRF, deterministic fallback) on the action
    target as a query; records the ranked hits with per-channel rankings in
    the action's result_payload. Falls back to a cross-case search when the
    case's own material yields nothing.
    """

    async def execute(self, action: Action) -> ExecutionResult:
        if action.action_type != "RETRIEVE" or not action.target:
            return ExecutionResult.empty_list(action)
        from case_engine.retrieval import search_evidence

        hits = await search_evidence(
            action.target, case_id=action.case_id, limit=8
        )
        channel = "in_case"
        if not hits:
            hits = await search_evidence(
                action.target, case_id=action.case_id, cross_case=True, limit=5
            )
            channel = "cross_case"
        if not hits:
            return ExecutionResult(
                OUTCOME_UNINFORMATIVE,
                notes="Hybrid retrieval found no matching evidence",
                diagnostics={"query": action.target[:200]},
            )
        return ExecutionResult(
            OUTCOME_INFORMATIVE_NO_EVIDENCE,
            notes=(
                f"Retrieved {len(hits)} item(s) ({channel}); ranked ids in "
                "result_payload.diagnostics.retrieved"
            ),
            diagnostics={
                "query": action.target[:200],
                "channel": channel,
                "retrieved": [
                    {
                        "item_id": h.item_id,
                        "item_type": h.item_type,
                        "score": h.score,
                        "case_id": h.case_id,
                        "excerpt": h.content[:120],
                    }
                    for h in hits
                ],
            },
        )


class CompareExecutor:
    """COMPARE executor: structured comparison of two conflicting claims.

    The PDF's motivating example: "Compare the two conflicting reports and
    determine whether the latency increase preceded or followed the
    deployment." The executor parses the '<claim_a> vs <claim_b>' target
    (the format generate_actions uses for contradictions), loads both
    claims with their evidence and temporal windows, orders them on the
    case timeline, and writes a derived comparison record as MODEL_INFERENCE
    evidence (weight 0.0 by the self-confirmation guard — ICE's own
    inference must never confirm ICE's own hypotheses; its value is the
    recorded reasoning, and its lineage links back to the sources).
    """

    async def execute(self, action: Action) -> ExecutionResult:
        if action.action_type != "COMPARE" or not action.target:
            return ExecutionResult.empty_list(action)

        pool = await get_pool()
        if pool is None:
            raise DatabaseUnavailableError("Cannot compare: no DB pool")

        # Locate the two claims this action compares
        # (target format: '<claim_a_id> vs <claim_b_id>').
        parts = [p.strip() for p in (action.target or "").split(" vs ")]
        claim_ids = [p for p in parts if p]
        rows = []
        if len(claim_ids) == 2:
            async with pool.acquire() as conn:
                for cid in claim_ids:
                    r = await conn.fetchrow(
                        """
                        SELECT c.claim_id, c.statement, c.valid_from,
                               c.valid_until, c.confidence,
                               c.supporting_evidence_ids
                        FROM claims c
                        WHERE c.claim_id = $1 AND c.case_id = $2
                        """,
                        cid, action.case_id,
                    )
                    if r is not None:
                        rows.append(dict(r))
        if len(rows) < 2:
            return ExecutionResult(
                OUTCOME_UNSUPPORTED,
                notes=(
                    "COMPARE target did not resolve to two claims in this "
                    "case (expected '<claim_a_id> vs <claim_b_id>'); edit "
                    "the target or link the contradiction first."
                ),
            )

        a, b = rows[0], rows[1]

        def _ts(x):
            return x["valid_from"] or x["valid_until"]

        ordering = "incomparable"
        if _ts(a) is not None and _ts(b) is not None:
            if _ts(a) < _ts(b):
                ordering = "a_precedes_b"
            elif _ts(b) < _ts(a):
                ordering = "b_precedes_a"
            else:
                ordering = "simultaneous"

        support_a = len(a["supporting_evidence_ids"] or [])
        support_b = len(b["supporting_evidence_ids"] or [])
        better_supported = (
            "a" if a["confidence"] * (1 + support_a)
            >= b["confidence"] * (1 + support_b) else "b"
        )

        from case_engine.evidence import ingest_evidence, _create_source

        content = (
            f"COMPARISON (derived by ICE compare executor for action "
            f"{action.action_id}).\n"
            f"Claim A [{a['claim_id']}]: {a['statement']}\n"
            f"  (confidence {a['confidence']:.2f}, {support_a} supporting "
            f"evidence, valid_from {a['valid_from']})\n"
            f"Claim B [{b['claim_id']}]: {b['statement']}\n"
            f"  (confidence {b['confidence']:.2f}, {support_b} supporting "
            f"evidence, valid_from {b['valid_from']})\n"
            f"Temporal ordering: {ordering}.\n"
            f"Better supported by current evidence: {better_supported}.\n"
            f"Interpretation guard: this is ICE-derived MODEL_INFERENCE "
            f"evidence (weight 0.0 by the self-confirmation guard). A "
            f"change over time, a scope difference, or an unobserved "
            f"intermediate event may reconcile both claims — see "
            f"case_engine.timeline and case_engine.causal."
        )
        # Hari ICE fix pass: ingest_evidence requires a source_id FK (the
        # call previously omitted it, so every COMPARE execution crashed
        # with a TypeError after doing the comparison work — found live by
        # the benchmark's --execute-actions run, not by the suite, because
        # no test executed a COMPARE action end-to-end). The derived record
        # gets its own TOOL source so the provenance chain stays honest.
        compare_source = await _create_source(
            action.case_id,
            "TOOL",
            source_uri=f"ice://compare/{action.action_id}",
            metadata={
                "derivation": "compare_executor",
                "action_id": action.action_id,
                "compared_claims": [a["claim_id"], b["claim_id"]],
            },
        )
        ev = await ingest_evidence(
            case_id=action.case_id,
            content=content,
            source_id=compare_source.source_id,
            evidence_type="MODEL_INFERENCE",
            metadata={
                "derived_from": (
                    list(a["supporting_evidence_ids"] or [])
                    + list(b["supporting_evidence_ids"] or [])
                ),
                "derivation": "compare_executor",
                "action_id": action.action_id,
                "compared_claims": [a["claim_id"], b["claim_id"]],
                "ordering": ordering,
            },
        )
        return ExecutionResult(
            OUTCOME_EVIDENCE_PRODUCED,
            evidence_ids=[ev.evidence_id],
            notes=f"Comparison record {ev.evidence_id}: ordering={ordering}",
            diagnostics={
                "claim_a": a["claim_id"],
                "claim_b": b["claim_id"],
                "ordering": ordering,
                "better_supported": better_supported,
            },
        )


class FindCounterevidenceExecutor:
    """FIND_COUNTEREVIDENCE executor: actually obtain the counterevidence.

    Second-pass audit: "even a proposed falsification action does not
    automatically obtain the counterevidence it describes." This executor
    runs the CONTRASTIVE search (retrieval module) against the target
    hypothesis — deliberately hunting for material that challenges it
    rather than echoing it — links every hit to the hypothesis with a
    CONTRADICTS evidence→hypothesis edge (provenance in edge metadata), and
    records the search provenance. Finding nothing is INFORMATIVE: the
    hypothesis survived a genuine falsification attempt.
    """

    async def execute(self, action: Action) -> ExecutionResult:
        if action.action_type != "FIND_COUNTEREVIDENCE" or not action.target:
            return ExecutionResult.empty_list(action)

        from case_engine.hypothesis import (
            list_hypotheses_for_case as _list_h,
        )

        # Resolve the hypothesis: expected_outcome carries
        # "Find evidence that would weaken: <statement[:100]>".
        hypothesis = None
        prefix = ""
        if action.expected_outcome and "weaken:" in action.expected_outcome:
            prefix = action.expected_outcome.split("weaken:", 1)[1].strip().lower()
        hypotheses = await _list_h(action.case_id)
        for h in hypotheses:
            if prefix and h.statement.lower().startswith(prefix[:40]):
                hypothesis = h
                break
        if hypothesis is None and hypotheses:
            # fall back to the leading hypothesis
            from case_engine.hypothesis import get_current_version
            best, best_conf = None, -1.0
            for h in hypotheses:
                try:
                    v = await get_current_version(h.hypothesis_id)
                except Exception:
                    continue
                if v.confidence > best_conf:
                    best, best_conf = h, v.confidence
            hypothesis = best
        if hypothesis is None:
            return ExecutionResult(
                OUTCOME_UNSUPPORTED,
                notes="No hypothesis to seek counterevidence against",
            )

        from case_engine.retrieval import contrastive_search
        from case_engine.evidence_graph import add_edge, EdgeRelation

        hits = await contrastive_search(
            hypothesis.statement, case_id=action.case_id, limit=6
        )
        linked: list[str] = []
        for h in hits:
            if h.item_type != "evidence":
                continue
            try:
                await add_edge(
                    case_id=action.case_id,
                    from_node=h.item_id,
                    from_type="evidence",
                    to_node=hypothesis.hypothesis_id,
                    to_type="hypothesis",
                    relation=EdgeRelation.CONTRADICTS,
                    weight=round(min(1.0, h.score), 3),
                    metadata={
                        "origin": "find_counterevidence_executor",
                        "action_id": action.action_id,
                        "search_score": h.score,
                        "factors": h.factors,
                    },
                )
                linked.append(h.item_id)
            except Exception as exc:
                logger.debug("contradicts edge failed for %s: %s", h.item_id, exc)

        if not hits:
            return ExecutionResult(
                OUTCOME_INFORMATIVE_NO_EVIDENCE,
                notes=(
                    f"Contrastive search found NO counterevidence for "
                    f"hypothesis {hypothesis.hypothesis_id} — the hypothesis "
                    f"survived this falsification attempt (absence of "
                    f"findings is not proof of absence)."
                ),
                diagnostics={
                    "hypothesis_id": hypothesis.hypothesis_id,
                    "query_target": hypothesis.statement[:160],
                },
            )
        return ExecutionResult(
            OUTCOME_EVIDENCE_PRODUCED,
            evidence_ids=linked,
            notes=(
                f"Linked {len(linked)} counterevidence item(s) to hypothesis "
                f"{hypothesis.hypothesis_id} via CONTRADICTS edges"
            ),
            diagnostics={
                "hypothesis_id": hypothesis.hypothesis_id,
                "hits": [
                    {"item_id": h.item_id, "item_type": h.item_type,
                     "score": h.score, "excerpt": h.content[:100]}
                    for h in hits
                ],
            },
        )


class RunTestExecutor:
    """RUN_TEST executor: SAFE, deterministic tests only.

    Design rule (PDF): "Run safe, explicitly authorised tests" — there is
    no code sandbox here, and there never will be an implicit one. The
    supported test vocabulary is deliberately tiny and side-effect-free:

        temporal_consistency  — timeline.check_temporal_consistency
        state_change          — timeline.explain_state_change (which events
                                fall inside the healthy→failing window)
        ach_diagnostics       — causal.build_ach_matrix diagnostics

    Anything else is UNSUPPORTED with an explicit reason — the operator
    can wire a real sandbox executor via the ``executor`` argument.
    """

    _SUPPORTED = ("temporal_consistency", "state_change", "ach_diagnostics")

    async def execute(self, action: Action) -> ExecutionResult:
        if action.action_type != "RUN_TEST" or not action.target:
            return ExecutionResult.empty_list(action)
        target = (action.target or "").lower()

        if "temporal" in target and "consisten" in target:
            from case_engine.timeline import check_temporal_consistency
            report = await check_temporal_consistency(action.case_id)
            conflicts = report["conflicts"]
            return ExecutionResult(
                OUTCOME_INFORMATIVE_NO_EVIDENCE,
                notes=(
                    f"Temporal consistency: {len(conflicts)} overlapping "
                    f"window conflict(s) over {report['checked_pairs']} pairs"
                ),
                diagnostics=report,
            )
        if "state" in target and "change" in target:
            from case_engine.timeline import explain_state_change
            report = await explain_state_change(action.case_id)
            return ExecutionResult(
                OUTCOME_INFORMATIVE_NO_EVIDENCE,
                notes=(
                    "State-change analysis: "
                    + ("transition located; candidates in diagnostics"
                       if report.get("transition_found")
                       else "no healthy→failing transition located")
                ),
                diagnostics=report,
            )
        if "ach" in target or "matrix" in target or "competing" in target:
            from case_engine.causal import build_ach_matrix
            matrix = await build_ach_matrix(action.case_id)
            return ExecutionResult(
                OUTCOME_INFORMATIVE_NO_EVIDENCE,
                notes=(
                    "ACH diagnostics: least-contradicted="
                    f"{matrix['diagnostics']['least_contradicted']}"
                ),
                diagnostics={
                    "least_contradicted": matrix["diagnostics"]["least_contradicted"],
                    "most_discriminating": matrix["diagnostics"]["most_discriminating_claims"],
                    "hypotheses": matrix["hypotheses"],
                },
            )
        return ExecutionResult(
            OUTCOME_UNSUPPORTED,
            notes=(
                "RUN_TEST supports only the safe deterministic test "
                f"vocabulary {self._SUPPORTED} (referenced via the action "
                "target); arbitrary test execution requires an explicitly "
                "authorized sandbox executor."
            ),
        )


class WaitExecutor:
    """WAIT executor: schedule a bounded re-evaluation.

    A WAIT action records WHY the investigation is pausing and what should
    wake it (the target text), as an information gap — so "wait" is real,
    inspectable state instead of a silent no-op.
    """

    async def execute(self, action: Action) -> ExecutionResult:
        if action.action_type != "WAIT" or not action.target:
            return ExecutionResult.empty_list(action)
        gap = await create_gap(
            case_id=action.case_id,
            needed_evidence=f"Re-evaluate after: {action.target}",
            why_needed=(
                f"WAIT action {action.action_id} — investigation paused "
                "pending this condition"
            ),
            discriminating_hypothesis_ids=[],
        )
        return ExecutionResult(
            OUTCOME_INFORMATIVE_NO_EVIDENCE,
            notes=f"Wake-up condition recorded as gap {gap.gap_id}",
            diagnostics={"gap_id": gap.gap_id},
        )


class AbstainExecutor:
    """ABSTAIN executor: record the abstention and its rationale.

    Abstention is a legitimate decision outcome (decide() rule ladder) —
    this executor makes an executed ABSTAIN action leave its reasoning
    behind as an information gap instead of nothing.
    """

    async def execute(self, action: Action) -> ExecutionResult:
        if action.action_type != "ABSTAIN" or not action.target:
            return ExecutionResult.empty_list(action)
        gap = await create_gap(
            case_id=action.case_id,
            needed_evidence=f"Abstention rationale: {action.target}",
            why_needed=(
                f"ABSTAIN action {action.action_id} — recorded so the "
                "abstention is auditable, not silent"
            ),
            discriminating_hypothesis_ids=[],
        )
        return ExecutionResult(
            OUTCOME_INFORMATIVE_NO_EVIDENCE,
            notes=f"Abstention recorded as gap {gap.gap_id}",
            diagnostics={"gap_id": gap.gap_id},
        )


class RevisitHypothesisExecutor:
    """REVISIT_HYPOTHESIS executor: re-derive what is known now.

    Recomputes every hypothesis confidence from the current evidence graph,
    collects the unexplained observations (claims no hypothesis covers),
    and — when the case is explanation-starved (fewer than two non-terminal
    hypotheses) — runs the induction engine to generate new candidates.
    """

    async def execute(self, action: Action) -> ExecutionResult:
        if action.action_type != "REVISIT_HYPOTHESIS":
            return ExecutionResult.empty_list(action)

        from case_engine.hypothesis import (
            list_hypotheses_for_case as _list_h,
            recompute_hypothesis_confidence as _recompute,
            get_current_version as _current,
        )
        from case_engine.induction import (
            induce_hypotheses,
            unexplained_observations,
        )

        changes: list[dict] = []
        for h in await _list_h(action.case_id):
            try:
                before = (await _current(h.hypothesis_id)).confidence
                await _recompute(h.hypothesis_id, cause_event_id=action.action_id)
                after = (await _current(h.hypothesis_id)).confidence
                if abs(after - before) > 0.001:
                    changes.append(
                        {"hypothesis_id": h.hypothesis_id,
                         "before": before, "after": after}
                    )
            except Exception as exc:
                logger.debug("recompute failed during revisit: %s", exc)

        unexplained = await unexplained_observations(action.case_id)

        induction_summary = None
        active = [
            h for h in await _list_h(action.case_id)
            if h.status not in ("FALSIFIED",)
        ]
        if len(active) < 2:
            result = await induce_hypotheses(action.case_id, create=True)
            induction_summary = {
                "path": result["generation_path"],
                "created": result["created_hypothesis_ids"],
                "candidates": len(result["candidates"]),
            }

        return ExecutionResult(
            OUTCOME_INFORMATIVE_NO_EVIDENCE,
            notes=(
                f"Recomputed {len(changes)} hypothesis confidence change(s); "
                f"{len(unexplained)} unexplained observation(s)"
                + ("; induction ran" if induction_summary else "")
            ),
            diagnostics={
                "confidence_changes": changes,
                "unexplained_observations": unexplained[:5],
                "induction": induction_summary,
            },
        )


# Module-level default executor (improvisation G: closed-world by default).
_DEFAULT_EXECUTOR: ActionExecutor = NullExecutor()


def default_executor_for(action: Action) -> ActionExecutor:
    """Return the real executor for the action's type (P1 completion).

    Every action type now has a contract and an executor:
        SEARCH                 -> WebSearchExecutor (URL ingestion)
        INSPECT                -> FileInspectExecutor (file ingestion)
        ASK_HUMAN              -> HumanAskExecutor (records the gap)
        RETRIEVE               -> RetrieveExecutor (hybrid evidence search)
        COMPARE                -> CompareExecutor (structured comparison)
        FIND_COUNTEREVIDENCE   -> FindCounterevidenceExecutor (contrastive
                                  search + CONTRADICTS linking)
        RUN_TEST               -> RunTestExecutor (safe deterministic tests)
        WAIT                   -> WaitExecutor (records the wake condition)
        ABSTAIN                -> AbstainExecutor (records the rationale)
        REVISIT_HYPOTHESIS     -> RevisitHypothesisExecutor (recompute +
                                  induction when starved)
    NullExecutor remains injectable for closed-world benchmark runs.
    """
    mapping = {
        "SEARCH": WebSearchExecutor,
        "INSPECT": FileInspectExecutor,
        "ASK_HUMAN": HumanAskExecutor,
        "RETRIEVE": RetrieveExecutor,
        "COMPARE": CompareExecutor,
        "FIND_COUNTEREVIDENCE": FindCounterevidenceExecutor,
        "RUN_TEST": RunTestExecutor,
        "WAIT": WaitExecutor,
        "ABSTAIN": AbstainExecutor,
        "REVISIT_HYPOTHESIS": RevisitHypothesisExecutor,
    }
    cls = mapping.get(action.action_type)
    return cls() if cls else NullExecutor()


async def _run_action_executor(
    action: Action,
    executor: Optional[ActionExecutor] = None,
) -> ExecutionResult:
    """Execute a specific action type via the ActionExecutor protocol.

    The executor returns a structured ExecutionResult; legacy
    ``list[str]``-returning executors are wrapped for compatibility.
    """
    if executor is None:
        executor = _DEFAULT_EXECUTOR
    result = await executor.execute(action)
    if isinstance(result, list):  # legacy executor compatibility
        result = ExecutionResult(
            OUTCOME_EVIDENCE_PRODUCED if result else OUTCOME_UNINFORMATIVE,
            evidence_ids=result,
        )
    return result


async def execute_action(
    action_id: str,
    executor: Optional[ActionExecutor] = None,
) -> Action:
    """Execute an approved action.

    Preconditions: action.status == 'approved' (or already 'completed' —
        re-execution is IDEMPOTENT: it returns the recorded result without
        re-running effects, the engine-side half of the deferred
        "idempotency keys" item).
    Postconditions:
        On success: status='completed' only when the outcome produced
            evidence or was legitimately informative; result_evidence_ids,
            result_payload (outcome + diagnostics) and outcome are
            persisted; action_executed event emitted (transactionally with
            the status update).
        An evidence-expecting action type whose outcome is UNINFORMATIVE or
            UNSUPPORTED lands in status='failed' with outcome set — the P1
            rule "an action should not be marked as successfully completed
            merely because an executor returned an empty list".
        On executor exception: status='failed', action_failed event,
            exception re-raised.
    """
    action = await get_action(action_id)
    if action.status == "completed":
        # Idempotent re-execution: return the recorded result untouched.
        return action
    if action.status != "approved":
        raise ValueError(f"Action {action_id} is not 'approved' (current: {action.status})")

    # Part 2 §4.2: when the caller passes no executor, dispatch on the
    # action type (every type now has a real executor).
    if executor is None:
        executor = default_executor_for(action)

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot execute: no DB pool")

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE actions SET status = 'executing' WHERE action_id = $1", action_id,
        )

    try:
        result = await _run_action_executor(action, executor)
        evidence_ids = list(result.evidence_ids or [])

        # --- outcome semantics (P1) ---------------------------------------
        if result.outcome == OUTCOME_EVIDENCE_PRODUCED and evidence_ids:
            final_status = "completed"
        elif result.outcome == OUTCOME_INFORMATIVE_NO_EVIDENCE:
            final_status = "completed"
        else:  # UNINFORMATIVE or UNSUPPORTED (or EVIDENCE_PRODUCED w/o ids)
            final_status = (
                "failed"
                if action.action_type in _EVIDENCE_EXPECTING_TYPES
                else "completed"
            )

        import json as _json

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE actions
                    SET status = $1,
                        result_evidence_ids = $2::text[],
                        result_payload = $3::jsonb,
                        outcome = $4,
                        completed_at = $5
                    WHERE action_id = $6
                    """,
                    final_status, evidence_ids,
                    _json.dumps(result.to_payload(), default=str),
                    result.outcome, datetime.now(timezone.utc), action_id,
                )
                await _emit_event(action.case_id, "action_executed", {
                    "action_id": action_id,
                    "result_evidence_ids": evidence_ids,
                    "outcome": result.outcome,
                    "notes": result.notes[:300],
                }, conn=conn)
        if final_status == "failed":
            logger.warning(
                "Action %s (%s) recorded as failed: outcome=%s (%s)",
                action_id, action.action_type, result.outcome, result.notes[:120],
            )
    except Exception as exc:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE actions SET status = 'failed', completed_at = $1 "
                    "WHERE action_id = $2",
                    datetime.now(timezone.utc), action_id,
                )
                await _emit_event(action.case_id, "action_failed", {
                    "action_id": action_id,
                    "error": str(exc),
                }, conn=conn)
        raise

    return await get_action(action_id)
