"""case_engine/case.py — Case CRUD: the root object of the ICE state plane.

Owner: ICE foundation (spec Part 2 §2).
Purpose: create, load, list, status-transition and summarize investigation
    cases. A case is the durable root that survives sessions; every other
    ICE table hangs off ``cases.case_id``.
Allowed downstream dependencies: ``db.connection.get_pool``,
    ``case_engine._emit_event``, ``case_engine.exceptions``,
    ``case_engine.schemas.Case``.

Conventions honored (spec §3.3): every public function is async and fully
annotated; ``await get_pool()`` raises ``DatabaseUnavailableError`` when the
pool is None; writes run inside ``async with pool.acquire() as conn:``; every
state mutation emits exactly one event via ``_emit_event``; all timestamps
are timezone-aware UTC; row → model mapping is explicit (no **row splat).

Audit-mandated addition (worklog ice-0, gap #40): a naive
``decision_deadline`` passed to ``create_case`` is promoted to UTC instead of
being rejected by Postgres or silently stored offset-less.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from db.connection import get_pool
from case_engine import _emit_event
from case_engine.exceptions import CaseNotFoundError, DatabaseUnavailableError
from case_engine.schemas import Case

logger = logging.getLogger(__name__)


def _row_to_case(row) -> Case:
    """Map an asyncpg record to a Case.

    Preconditions:
        row contains all columns from the cases table (spec §5.1).
    Postconditions:
        Returns a Case with every field populated.
    Raises:
        KeyError: if any required column is missing.

    Part 2 §1.2: ``case_type`` is read with a KeyError/IndexError guard so a
    database that has not yet applied migration 015 maps rows with the
    "debug" default instead of crashing.
    """
    # Part 2 §1.2 pattern: asyncpg Record raises KeyError on a missing
    # column; migration 015 not being applied is tolerable at read time.
    case_type = "debug"
    try:
        case_type = row["case_type"]
    except (KeyError, IndexError):
        pass

    return Case(
        case_id=row["case_id"],
        title=row["title"],
        objective=row["objective"],
        decision_deadline=row["decision_deadline"],
        required_confidence=row["required_confidence"],
        cost_budget_tool_calls=row["cost_budget_tool_calls"],
        cost_budget_tokens=row["cost_budget_tokens"],
        status=row["status"],
        case_type=case_type,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def create_case(
    title: str,
    objective: str,
    decision_deadline: Optional[datetime] = None,
    required_confidence: Optional[float] = None,
    cost_budget_tool_calls: int = 100,
    cost_budget_tokens: int = 500000,
    case_type: str = "debug",
) -> Case:
    """Create a new case.

    Preconditions:
        title is non-empty.
        objective is non-empty.
        case_type is one of "debug", "research", "explore" (Part 2 §1.4).
        required_confidence, if provided, ∈ [0.0, 1.0].
        cost_budget_tool_calls > 0.
        cost_budget_tokens > 0.
        decision_deadline, if provided, is timezone-aware (a naive datetime
        is promoted to UTC per audit gap #40).
    Postconditions:
        A row exists in cases with a fresh case_id.
        A case_created event exists in case_events (payload includes
        case_type — Part 2 §1.4).
        The returned Case matches the DB row.
    Part 2 §1.4 required_confidence defaulting:
        ``required_confidence`` is Optional so the mode can pick the
        default: 0.85 for debug (the confidence ladder drives resolution),
        0.0 for research/explore (their decide() ladders do not use a
        confidence threshold; an explicit caller value always wins).
    Raises:
        ValueError: if any precondition is violated.
        DatabaseUnavailableError: if the DB pool is None.
    Side effects:
        INSERT INTO cases (1 row).
        _emit_event("case_created", {...}).
    """
    if not title or not title.strip():
        raise ValueError("title must be non-empty")
    if not objective or not objective.strip():
        raise ValueError("objective must be non-empty")
    if case_type not in ("debug", "research", "explore"):
        raise ValueError(
            f"Invalid case_type: {case_type}. Must be debug, research, or explore."
        )
    if required_confidence is None:
        required_confidence = 0.85 if case_type == "debug" else 0.0
    if not (0.0 <= required_confidence <= 1.0):
        raise ValueError("required_confidence must be in [0.0, 1.0]")
    if cost_budget_tool_calls <= 0:
        raise ValueError("cost_budget_tool_calls must be > 0")
    if cost_budget_tokens <= 0:
        raise ValueError("cost_budget_tokens must be > 0")

    # audit gap #40 fix: never persist a naive datetime
    if decision_deadline is not None and decision_deadline.tzinfo is None:
        decision_deadline = decision_deadline.replace(tzinfo=timezone.utc)

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot create case: no DB pool")

    case_id = f"case_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO cases (
                case_id, title, objective, decision_deadline,
                required_confidence, cost_budget_tool_calls, cost_budget_tokens,
                case_type, status, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'open', $9, $9)
            """,
            case_id,
            title.strip(),
            objective.strip(),
            decision_deadline,
            required_confidence,
            cost_budget_tool_calls,
            cost_budget_tokens,
            case_type,
            now,
        )

    await _emit_event(
        case_id,
        "case_created",
        {
            "title": title.strip(),
            "objective": objective.strip(),
            "decision_deadline": (
                decision_deadline.isoformat() if decision_deadline else None
            ),
            "required_confidence": required_confidence,
            "case_type": case_type,
        },
    )

    logger.info("Case created: %s — %s", case_id, title)
    return await get_case(case_id)


async def get_case(case_id: str) -> Case:
    """Load a case by ID.

    Preconditions:
        case_id is non-empty.
    Postconditions:
        returned Case matches the DB row.
    Raises:
        ValueError: if case_id is empty.
        CaseNotFoundError: if no row exists.
        DatabaseUnavailableError: if the DB pool is None.
    Side effects:
        none (read-only).
    """
    if not case_id:
        raise ValueError("case_id must be non-empty")

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot load case: no DB pool")

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM cases WHERE case_id = $1", case_id)

    if row is None:
        raise CaseNotFoundError(f"Case not found: {case_id}")

    return _row_to_case(row)


async def update_case_status(
    case_id: str,
    new_status: Literal[
        "open", "investigating", "deciding", "resolved", "abandoned"
    ],
    reason: Optional[str] = None,
) -> Case:
    """Change a case's status.

    Preconditions:
        case_id exists.
        new_status is one of the five allowed values.
    Postconditions:
        cases.status == new_status.
        cases.updated_at == now.
        A case_status_changed event exists (unless the status was already
        ``new_status``, in which case this is a no-op and no event is
        emitted — exactly one event per actual mutation).
    Raises:
        CaseNotFoundError: if case_id does not exist.
        ValueError: if new_status is not allowed.
        DatabaseUnavailableError: if the DB pool is None.
    Side effects:
        UPDATE cases (1 row).
        _emit_event("case_status_changed", {...}).
    """
    allowed = {"open", "investigating", "deciding", "resolved", "abandoned"}
    if new_status not in allowed:
        raise ValueError(f"Invalid status: {new_status}. Allowed: {sorted(allowed)}")

    current = await get_case(case_id)

    if current.status == new_status:
        return current

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot update case: no DB pool")

    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE cases SET status = $1, updated_at = $2 WHERE case_id = $3",
            new_status,
            now,
            case_id,
        )

    await _emit_event(
        case_id,
        "case_status_changed",
        {
            "old_status": current.status,
            "new_status": new_status,
            "reason": reason or "",
        },
    )

    return await get_case(case_id)


async def list_cases(
    status: Optional[
        Literal["open", "investigating", "deciding", "resolved", "abandoned"]
    ] = None,
    limit: int = 50,
    case_type: Optional[Literal["debug", "research", "explore"]] = None,
) -> list[Case]:
    """List cases, optionally filtered by status and/or case_type.

    Preconditions:
        0 < limit <= 1000.
        status is None or one of the five allowed values.
        case_type is None or one of debug/research/explore (Part 2 §3.4).
    Postconditions:
        Returns up to limit cases ordered by updated_at DESC, filtered by
        the provided status and/or case_type.
    Raises:
        ValueError: on invalid limit, status, or case_type.
        DatabaseUnavailableError: if the DB pool is None.
    Side effects:
        none.
    """
    if not (0 < limit <= 1000):
        raise ValueError("limit must be in (0, 1000]")
    allowed = {"open", "investigating", "deciding", "resolved", "abandoned"}
    if status is not None and status not in allowed:
        raise ValueError(f"Invalid status: {status}")
    if case_type is not None and case_type not in ("debug", "research", "explore"):
        raise ValueError(f"Invalid case_type: {case_type}")

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot list cases: no DB pool")

    # Part 2 §3.4: the doc's four-branch query collapsed into one dynamic
    # WHERE — every value stays a bound parameter ($n), never interpolated.
    clauses: list[str] = []
    params: list = []
    if status is not None:
        params.append(status)
        clauses.append(f"status = ${len(params)}")
    if case_type is not None:
        params.append(case_type)
        clauses.append(f"case_type = ${len(params)}")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM cases {where} ORDER BY updated_at DESC LIMIT ${len(params)}",
            *params,
        )

    return [_row_to_case(row) for row in rows]


async def get_case_summary(case_id: str) -> dict:
    """Return a summary of a case's current state.

    Preconditions:
        case_id exists.
    Postconditions:
        returns a dict with the keys listed below.
    Raises:
        CaseNotFoundError: if case_id does not exist.
        DatabaseUnavailableError: if the DB pool is None.
    Side effects:
        none (read-only).

    Return shape:
        {
          "case_id": str,
          "title": str,
          "objective": str,
          "case_type": str,                      # NEW (Part 2 §3.4)
          "status": str,
          "required_confidence": float,
          "decision_deadline": str | None,
          "counts": {
              "evidence_active": int,
              "evidence_total": int,
              "claims": int,
              "hypotheses": int,
              "hypotheses_supported": int,
              "contradictions_active": int,
              "gaps_unresolved": int,
              "threads_active": int,
              "actions_proposed": int,
              "decisions": int,
              "events": int,
          },
          "leading_hypothesis": {
              "hypothesis_id": str,
              "statement": str,
              "confidence": float,
              "version_number": int,
          } | None,
          "next_action": {
              "action_id": str,
              "action_type": str,
              "target": str | None,
              "priority": float,
          } | None,
        }
    """
    case = await get_case(case_id)

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot summarize case: no DB pool")

    async with pool.acquire() as conn:
        counts_row = await conn.fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM evidence WHERE case_id = $1 AND status = 'ACTIVE') AS evidence_active,
                (SELECT COUNT(*) FROM evidence WHERE case_id = $1) AS evidence_total,
                (SELECT COUNT(*) FROM claims WHERE case_id = $1) AS claims,
                (SELECT COUNT(*) FROM hypotheses WHERE case_id = $1) AS hypotheses,
                (SELECT COUNT(*) FROM hypotheses WHERE case_id = $1 AND status = 'SUPPORTED') AS hypotheses_supported,
                (SELECT COUNT(*) FROM contradictions WHERE case_id = $1 AND status = 'active') AS contradictions_active,
                (SELECT COUNT(*) FROM information_gaps WHERE case_id = $1 AND resolved_at IS NULL) AS gaps_unresolved,
                (SELECT COUNT(*) FROM investigation_threads WHERE case_id = $1 AND is_active = TRUE) AS threads_active,
                (SELECT COUNT(*) FROM actions WHERE case_id = $1 AND status = 'proposed') AS actions_proposed,
                (SELECT COUNT(*) FROM decisions WHERE case_id = $1) AS decisions,
                (SELECT COUNT(*) FROM case_events WHERE case_id = $1) AS events
            """,
            case_id,
        )

        leading_row = await conn.fetchrow(
            """
            SELECT h.hypothesis_id, h.statement, hv.confidence, hv.version_number
            FROM hypotheses h
            JOIN hypothesis_versions hv ON hv.version_id = h.current_version_id
            WHERE h.case_id = $1
            ORDER BY hv.confidence DESC
            LIMIT 1
            """,
            case_id,
        )

    # Compute next_action via the same function the pipeline uses
    # (merge spec Part 1 §5.6 P6): the old inline SQL approximated priority
    # as (EIG × impact) / (1 + cost), which could disagree with the NBA
    # engine's actual (EIG × impact × (1+falsif)) / (1+cost) + diversity −
    # redundancy − risk. Lazy import avoids the circular dependency
    # (case.py -> next_best_action.py -> case.py).
    from case_engine.next_best_action import select_next_best_action

    nba = await select_next_best_action(case_id)
    if nba is not None:
        action, priority = nba
        next_action_dict = {
            "action_id": action.action_id,
            "action_type": action.action_type,
            "target": action.target,
            "priority": priority,
        }
    else:
        next_action_dict = None

    return {
        "case_id": case.case_id,
        "title": case.title,
        "objective": case.objective,
        "case_type": case.case_type,
        "status": case.status,
        "required_confidence": case.required_confidence,
        "decision_deadline": (
            case.decision_deadline.isoformat() if case.decision_deadline else None
        ),
        "counts": dict(counts_row) if counts_row else {},
        "leading_hypothesis": dict(leading_row) if leading_row else None,
        "next_action": next_action_dict,
    }