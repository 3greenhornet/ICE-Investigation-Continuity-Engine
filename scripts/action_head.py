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
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


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


