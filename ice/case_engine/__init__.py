"""case_engine — Investigation Continuity Engine (ICE) core package.

Owner: ICE foundation (spec Part 1 §3.4, §6.10).
Purpose: package root for the ICE revamp of Hari. Defines the mandatory
    ``_emit_event`` helper — the ONLY function in the entire code base allowed
    to write to the ``case_events`` table — plus the ``CaseEvent`` payload
    model and the ``EVENT_TYPES`` allow-list (spec Part 1 §7).
Allowed downstream dependencies: ``db.connection.get_pool`` only. Submodules
    (case, evidence, claim, hypothesis, contradiction, information_gap,
    investigation_thread, evidence_graph, provenance, action,
    next_best_action, decision, falsification, replay, llm, ...) are NOT
    imported at module level here — they import ``_emit_event`` from this
    package, so importing them eagerly would be circular. ``from
    case_engine import case`` etc. still work via Python's normal submodule
    mechanism.

Hari ICE "addition and improvements" pass changes (see CHANGES.md):
    * EVENT_TYPES extended with the reasoning-loop event types (predictions,
      causal edges, plan, workspace, induction). Each addition has a §7-style
      table entry in CHANGES.md — they are not silent.
    * ``_emit_event`` accepts an optional ``conn`` (an asyncpg Connection).
      Passing one emits the event INSIDE the caller's transaction, closing
      the "mutation and event insert are separate DB operations" audit gap:
      either both the state change and its event commit, or neither does.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel

from db.connection import get_pool

logger = logging.getLogger(__name__)

# =====================================================================
# Event type allow-list (spec Part 1 §7: 22 event types + background_tick).
# Audit-mandated addition (worklog ice-0, gap #6): "background_tick".
#
# Hari ICE improvements pass — reasoning-loop event types. Every addition
# is documented with a payload table in CHANGES.md (the fixes-doc rule
# "adding a new event type requires a schema change and a spec table
# entry" is honored: the schema change is migrations 018/019, the table
# entries live in CHANGES.md §"Event allow-list additions"):
#     hypothesis_prediction_created / hypothesis_prediction_resolved
#         payload: {prediction_id, hypothesis_id, statement, probability}
#                  / {prediction_id, status, score}
#     causal_edge_proposed / causal_edge_reviewed
#         payload: {causal_edge_id, relation, cause, effect}
#                  / {causal_edge_id, decision: accepted|rejected, note}
#     plan_item_created / plan_item_status_changed / plan_revised
#         payload: {plan_item_id, description, status}
#                  / {plan_item_id, old_status, new_status, note}
#                  / {revision_id, reason, affected_items}
#     workspace_candidate_surfaced
#         payload: {workspace_item_id, category, score, factors}
#     induction_completed
#         payload: {case_id, candidates_generated, hypotheses_created}
# =====================================================================
EVENT_TYPES: frozenset[str] = frozenset({
    # case lifecycle
    "case_created",
    "case_status_changed",
    # evidence plane
    "evidence_ingested",
    "evidence_superseded",
    "evidence_status_changed",
    # claim plane
    "claim_extracted",
    "claim_status_changed",
    # hypothesis plane
    "hypothesis_created",
    "hypothesis_version_created",
    "hypothesis_status_changed",
    # contradiction plane
    "contradiction_detected",
    "contradiction_resolved",
    # information-gap plane
    "information_gap_created",
    "information_gap_resolved",
    # investigation-thread plane
    "investigation_thread_created",
    "investigation_thread_reinforced",
    "investigation_thread_deactivated",
    # action plane
    "action_proposed",
    "action_approved",
    "action_executed",
    "action_failed",
    # decision plane
    "decision_made",
    # audit gap #6 fix: endogenous tick heartbeat for case mode
    "background_tick",
    # --- Hari ICE improvements pass: reasoning loop --------------------
    # prediction plane (testable predictions, Metaculus-style records)
    "hypothesis_prediction_created",
    "hypothesis_prediction_resolved",
    # causal plane (proposed causal relationships, human-reviewed)
    "causal_edge_proposed",
    "causal_edge_reviewed",
    # plan plane (adaptive plan & checklist manager)
    "plan_item_created",
    "plan_item_status_changed",
    "plan_revised",
    # workspace plane (global attention candidates)
    "workspace_candidate_surfaced",
    # induction plane (hypothesis induction engine)
    "induction_completed",
    # goal plane (Evolving Project Model — intent graph, clarity levels,
    # provenance categories, supersede chains, maturity assessments)
    "goal_created",
    "goal_status_changed",
    "goal_edge_added",
    "goal_maturity_assessed",
    "goal_decomposition_proposed",
    # research plane (STORM-lite perspective-guided question discovery)
    "research_questions_generated",
    # pedagogy plane (LearnHarness cognitive scaffolding, probe, teach, practice)
    "pedagogy_session_started",
    "pedagogy_step_advanced",
    "pedagogy_check_evaluated",
    "pedagogy_practice_concluded",
})


class CaseEvent(BaseModel):
    """Row shape of the ``case_events`` table (spec Part 1 §6.10)."""

    event_id: str
    case_id: str
    event_type: str
    event_seq: int
    conversation_turn: Optional[int] = None
    wall_clock_time: datetime
    payload: dict


async def _emit_event(
    case_id: str,
    event_type: str,
    payload: dict,
    conversation_turn: Optional[int] = None,
    conn=None,
) -> None:
    """Append one event to the ``case_events`` log for ``case_id``.

    Preconditions:
        case_id is a non-empty string identifying an existing case row.
        event_type is one of the types in ``EVENT_TYPES``.
        payload is a JSON-serializable dict.
        conn, when provided, is an open asyncpg Connection participating in
            a transaction owned by the CALLER (event-consistency fix: the
            mutation and its event commit or roll back together).
    Postconditions:
        Exactly one row is inserted into ``case_events`` with the next
        per-case ``event_seq`` (MAX + 1) and a timezone-aware UTC wall
        clock time. With ``conn`` given, the insert happens inside the
        caller's transaction (the caller is responsible for commit/rollback).
    Raises:
        Nothing — a missing DB pool is logged and swallowed so that a
        logging failure never breaks the mutation that emitted the event.
        (When ``conn`` is provided and the insert fails, the exception
        propagates so the caller's transaction rolls back — an event must
        never be silently lost while its mutation commits.)
    Side effects:
        INSERT INTO case_events (1 row).
        Logs a warning (non-fatal) if event_type is not in EVENT_TYPES —
        silent event types are forbidden (spec §7).
    """
    if event_type not in EVENT_TYPES:
        logger.warning(
            "Emitting non-standard event_type '%s' for case %s "
            "(not in EVENT_TYPES; spec §7 forbids silent event types)",
            event_type,
            case_id,
        )

    async def _insert_on(connection) -> None:
        next_seq = await connection.fetchval(
            "SELECT COALESCE(MAX(event_seq), 0) + 1 FROM case_events "
            "WHERE case_id = $1",
            case_id,
        )
        await connection.execute(
            """
            INSERT INTO case_events (
                event_id, case_id, event_type, event_seq,
                conversation_turn, wall_clock_time, payload
            ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            """,
            str(uuid.uuid4()),
            case_id,
            event_type,
            next_seq,
            conversation_turn,
            datetime.now(timezone.utc),
            json.dumps(payload, default=str),
        )

    if conn is not None:
        # Transactional path: the caller owns commit/rollback. A failure
        # propagates so the paired mutation rolls back with the event.
        await _insert_on(conn)
        return

    pool = await get_pool()
    if not pool:
        logger.error("Cannot emit event: no DB pool")
        return
    async with pool.acquire() as acquired:
        await _insert_on(acquired)
