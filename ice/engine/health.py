# engine/health.py  (corrected)
from datetime import datetime, UTC
from typing import Dict, Any
import logging

from db.connection import get_pool

logger = logging.getLogger(__name__)


async def get_case_health_metrics(case_id: str) -> Dict[str, Any]:
    """
    ICE extension (inversion map, engine/health.py row): case health metrics.

    Aggregates the ICE state plane for one case: evidence freshness, open
    contradictions, unresolved gaps, hypothesis confidence spread, and event
    volume. Read-only; never raises on missing tables (returns status flags).
    """
    pool = await get_pool()
    if not pool:
        return {"case_id": case_id, "status": "degraded", "db": "unavailable"}

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    (SELECT COUNT(*) FROM evidence
                        WHERE case_id = $1 AND status = 'ACTIVE') AS evidence_active,
                    (SELECT COUNT(*) FROM evidence
                        WHERE case_id = $1 AND status IN ('STALE','SUPERSEDED','DISPUTED')
                    ) AS evidence_stale,
                    (SELECT COUNT(*) FROM contradictions
                        WHERE case_id = $1 AND status = 'active') AS contradictions_active,
                    (SELECT COUNT(*) FROM information_gaps
                        WHERE case_id = $1 AND resolved_at IS NULL) AS gaps_unresolved,
                    (SELECT COUNT(*) FROM investigation_threads
                        WHERE case_id = $1 AND is_active = TRUE) AS threads_active,
                    (SELECT COUNT(*) FROM actions
                        WHERE case_id = $1 AND status = 'proposed') AS actions_proposed,
                    (SELECT COUNT(*) FROM decisions WHERE case_id = $1) AS decisions,
                    (SELECT COUNT(*) FROM case_events WHERE case_id = $1) AS events
                """,
                case_id,
            )
            leading = await conn.fetchrow(
                """
                SELECT h.statement, hv.confidence
                FROM hypotheses h
                JOIN hypothesis_versions hv ON hv.version_id = h.current_version_id
                WHERE h.case_id = $1
                ORDER BY hv.confidence DESC
                LIMIT 1
                """,
                case_id,
            )
    except Exception as exc:
        logger.warning("Case health metrics failed for %s: %s", case_id, exc)
        return {"case_id": case_id, "status": "degraded", "error": str(exc)}

    counts: Dict[str, Any] = dict(row) if row else {}
    contradictions = int(counts.get("contradictions_active", 0))
    gaps = int(counts.get("gaps_unresolved", 0))
    leading_conf = float(leading["confidence"]) if leading else 0.0

    if contradictions > 0:
        verdict = "at_risk"
    elif gaps > 5:
        verdict = "uncertain"
    elif leading is None:
        verdict = "empty"
    else:
        verdict = "healthy"

    return {
        "case_id": case_id,
        "status": verdict,
        "counts": counts,
        "leading_hypothesis": {
            "statement": leading["statement"],
            "confidence": leading_conf,
        } if leading else None,
    }

async def get_health_metrics(session_id: str) -> Dict[str, Any]:
    """
    Single-pass health metric aggregation.
    Uses DISTINCT ON to get the latest state of each unique interest.
    """
    pool = await get_pool()
    if not pool:
        return {"error": "Database connection pool unavailable"}

    metrics_sql = """
        WITH trace_stats AS (
            SELECT 
                COUNT(*) as total_turns,
                COUNT(*) FILTER (WHERE retrieved_candidate_count = 0) as empty_turns,
                MAX(timestamp) as last_turn_time
            FROM decision_traces
            WHERE session_id = $1
        ),
        ledger_stats AS (
            SELECT
                COUNT(*) FILTER (WHERE event_type = 'promotion_attempt') as attempts,
                COUNT(*) FILTER (WHERE event_type = 'promotion_success') as successes
            FROM development_events
            WHERE session_id = $1
        ),
        current_interest_strengths AS (
            SELECT DISTINCT ON (interest_id) 
                interest_name,
                new_strength,
                event_type
            FROM development_events
            WHERE session_id = $1 
              AND interest_id IS NOT NULL
            ORDER BY interest_id, sequence_number DESC
        )
        SELECT 
            COALESCE(ts.total_turns, 0) as total_turns,
            COALESCE(ts.empty_turns, 0) as empty_turns,
            ts.last_turn_time,
            COALESCE(ls.attempts, 0) as attempts,
            COALESCE(ls.successes, 0) as successes,
            COALESCE(jsonb_agg(cis.interest_name) FILTER (
                WHERE cis.new_strength > 0.0 AND cis.event_type != 'promotion_decay'
            ), '[]'::jsonb) as active_interests,
            COALESCE(jsonb_agg(cis.interest_name) FILTER (
                WHERE cis.event_type = 'identity_anchor_formed'
            ), '[]'::jsonb) as identity_anchors
        FROM trace_stats ts
        CROSS JOIN ledger_stats ls
        CROSS JOIN current_interest_strengths cis
        GROUP BY ts.total_turns, ts.empty_turns, ts.last_turn_time, ls.attempts, ls.successes;
    """

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(metrics_sql, session_id)
            if not row:
                return {
                    "turns": 0,
                    "workspace_empty_rate": 0.0,
                    "promotion_attempts": 0,
                    "promotion_successes": 0,
                    "active_interests": [],
                    "identity_anchors": [],
                    "status": "initialized",
                    "timestamp": datetime.now(UTC).isoformat()
                }

            turns = row["total_turns"]
            empty_turns = row["empty_turns"] or 0
            empty_rate = (empty_turns / turns) if turns > 0 else 0.0

            return {
                "turns": turns,
                "workspace_empty_rate": round(empty_rate, 4),
                "promotion_attempts": row["attempts"] or 0,
                "promotion_successes": row["successes"] or 0,
                "active_interests": list(set(row["active_interests"] or [])),
                "identity_anchors": list(set(row["identity_anchors"] or [])),
                "last_activity": row["last_turn_time"].isoformat() if row["last_turn_time"] else None,
                "status": "healthy" if empty_rate < 0.01 else "degraded",
                "timestamp": datetime.now(UTC).isoformat()
            }
    except Exception as err:
        logger.error(f"Failed to generate health metrics: {err}", exc_info=True)
        return {"error": f"Metrics compilation failed: {str(err)}"}