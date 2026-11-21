"""case_engine/metrics.py — runtime case metrics (improvisation B).

Owner:      ICE revamp, reviewer audit improvisation B (moves the offline
            benchmark metrics IE / EU / ETC into a runtime module so
            `ice case metrics` and GET /cases/{id}/metrics can report live).
Purpose:    get_case_metrics(case_id) aggregates read-only per-case metrics:
            evidence trace coverage (ETC), investigation efficiency (IE),
            evidence utilization (EU), plus decision counts by type and
            action counts by status.
Deps:       db.connection, case_engine.exceptions, case_engine.provenance
            (ETC), case_engine.replay (event-sourced reconstruction for the
            IE entropy computation). Pure read-only.

Definitions:
    ETC — fraction of claims whose supporting evidence is traceable
          (delegated to provenance.compute_evidence_trace_coverage).
    IE  — ΔU_uncertainty / (1 + tool_cost), where ΔU is the drop in the
          normalized entropy of the hypothesis set (planning beliefs,
          Part 3 §0 rule 14) from the first decision_made event to the
          latest one, reconstructed via replay_case(up_to_event_seq), and
          tool_cost is the total number of actions recorded for the case.
          Returns 0.0 when not computable (fewer than two decisions, or no
          scoreable hypothesis set at either decision point).
    EU  — fraction of ACTIVE evidence referenced by at least one claim's
          supporting_evidence_ids.
"""

import logging
import math

from db.connection import get_pool
from case_engine.exceptions import DatabaseUnavailableError
from case_engine.provenance import compute_evidence_trace_coverage
from case_engine.replay import replay_case

logger = logging.getLogger(__name__)


def _normalized_entropy(confidences: list[float]) -> float | None:
    """Normalized entropy of a planning-belief set, or None if not computable.

    Same formula as compute_eig (Part 3 §7.2): normalize confidences to sum
    to 1, H = -sum(p log(p + 1e-9)), H_norm = H / log(n). Not computable when
    there are fewer than 2 confidences or the total is non-positive.
    """
    if len(confidences) < 2:
        return None
    total = sum(confidences)
    if total <= 0.0:
        return None
    probs = [c / total for c in confidences]
    H = -sum(p * math.log(p + 1e-9) for p in probs)
    H_max = math.log(len(probs))
    if H_max <= 0.0:
        return None
    return H / H_max


async def _investigation_efficiency(pool, case_id: str) -> float:
    """IE = ΔU_uncertainty / (1 + tool_cost); 0.0 when not computable."""
    async with pool.acquire() as conn:
        decision_rows = await conn.fetch(
            """
            SELECT event_seq FROM case_events
            WHERE case_id = $1 AND event_type = 'decision_made'
            ORDER BY event_seq ASC
            """,
            case_id,
        )
        if len(decision_rows) < 2:
            return 0.0
        tool_cost = await conn.fetchval(
            "SELECT COUNT(*) FROM actions WHERE case_id = $1", case_id,
        )

    first_seq = decision_rows[0]["event_seq"]
    latest_seq = decision_rows[-1]["event_seq"]

    try:
        first_state = await replay_case(case_id, up_to_event_seq=first_seq)
        latest_state = await replay_case(case_id, up_to_event_seq=latest_seq)
    except Exception as exc:
        logger.debug("IE replay failed for %s: %s", case_id, exc)
        return 0.0

    first_confidences = [h["confidence"] for h in first_state.hypotheses.values()]
    latest_confidences = [h["confidence"] for h in latest_state.hypotheses.values()]

    first_entropy = _normalized_entropy(first_confidences)
    latest_entropy = _normalized_entropy(latest_confidences)
    if first_entropy is None or latest_entropy is None:
        return 0.0

    delta_u = first_entropy - latest_entropy
    return delta_u / (1.0 + tool_cost)


async def _evidence_utilization(pool, case_id: str) -> float:
    """EU = referenced ACTIVE evidence / total ACTIVE evidence."""
    async with pool.acquire() as conn:
        total_active = await conn.fetchval(
            "SELECT COUNT(*) FROM evidence WHERE case_id = $1 AND status = 'ACTIVE'",
            case_id,
        )
        if not total_active:
            return 0.0
        referenced_active = await conn.fetchval(
            """
            SELECT COUNT(DISTINCT u.eid)
            FROM claims c
            CROSS JOIN LATERAL unnest(COALESCE(c.supporting_evidence_ids, '{}'::text[])) AS u(eid)
            JOIN evidence e ON e.evidence_id = u.eid
            WHERE c.case_id = $1 AND e.status = 'ACTIVE'
            """,
            case_id,
        )
    return referenced_active / total_active


async def get_case_metrics(case_id: str) -> dict:
    """Aggregate runtime metrics for a case. All read-only.

    Returns a dict with keys:
        case_id, evidence_trace_coverage, investigation_efficiency,
        evidence_utilization, decision_counts_by_type, actions_by_status.

    Individual metric failures are logged and reported as 0.0 / {} so one
    broken metric cannot take down a whole metrics endpoint; a missing pool
    raises DatabaseUnavailableError (Part 2 §0 rule 3).

    Raises:
        DatabaseUnavailableError: if the DB pool is None.
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot compute metrics: no DB pool")

    # ETC (delegated to provenance).
    try:
        etc = await compute_evidence_trace_coverage(case_id)
    except Exception as exc:
        logger.warning("ETC computation failed for %s: %s", case_id, exc)
        etc = 0.0

    # IE.
    try:
        ie = await _investigation_efficiency(pool, case_id)
    except Exception as exc:
        logger.warning("IE computation failed for %s: %s", case_id, exc)
        ie = 0.0

    # EU.
    try:
        eu = await _evidence_utilization(pool, case_id)
    except Exception as exc:
        logger.warning("EU computation failed for %s: %s", case_id, exc)
        eu = 0.0

    # Decision counts by type + actions by status.
    decision_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {}
    try:
        async with pool.acquire() as conn:
            decision_rows = await conn.fetch(
                "SELECT decision_type, COUNT(*) AS n FROM decisions "
                "WHERE case_id = $1 GROUP BY decision_type",
                case_id,
            )
            decision_counts = {r["decision_type"]: r["n"] for r in decision_rows}
            action_rows = await conn.fetch(
                "SELECT status, COUNT(*) AS n FROM actions "
                "WHERE case_id = $1 GROUP BY status",
                case_id,
            )
            action_counts = {r["status"]: r["n"] for r in action_rows}
    except Exception as exc:
        logger.warning("Count aggregation failed for %s: %s", case_id, exc)

    return {
        "case_id": case_id,
        "evidence_trace_coverage": etc,
        "investigation_efficiency": ie,
        "evidence_utilization": eu,
        "decision_counts_by_type": decision_counts,
        "actions_by_status": action_counts,
    }