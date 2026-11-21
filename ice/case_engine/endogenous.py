"""case_engine/endogenous.py — Case-mode endogenous candidates.

Surfaces material the user hasn't attended to recently: investigation
threads gone stale, evidence never cited by a claim, hypotheses stuck at
ambiguous confidence, contradictions aging without resolution.

Used by run_investigation_cycle-adjacent action generation
(_generate_actions_research / _generate_actions_explore in
case_engine.action) and, in Part 2-b scope, the
/cases/{id}/suggestions endpoint.

New file (ICE fixes doc Part 2 Section 5, Feature 2D; Correction D:
despite Part 1's claim, no such module existed in Oct 22 — this is new
work, not a port).
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from db.connection import get_pool
from case_engine.exceptions import DatabaseUnavailableError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunable thresholds (doc Part 2 §5.1 design note, honesty shortcut #2).
#
# HONEST NOTE: these are round numbers picked for "feels right", not values
# derived from observed user behavior. The principled version would derive
# from actual session data (average session length, typical
# time-to-resolution, how often this user engages). Exported as module
# constants so they can be tuned without touching the queries.
# ---------------------------------------------------------------------------
_UNCITED_EVIDENCE_HOURS = 1
_AGED_THREAD_DAYS = 1
_STUCK_HYPOTHESIS_DAYS = 2
_AGED_CONTRADICTION_DAYS = 2


async def collect_case_endogenous_candidates(
    case_id: str,
    current_turn: int = 0,
    max_per_source: int = 6,
) -> list[dict]:
    """Collect internal candidates for a case from persistent sources.

    Sources (each capped at max_per_source):
    1. Investigation threads with debt aging > 1 day
    2. Evidence marked ACTIVE but not yet extracted to any claim
       (evidence.extracted_claim_ids = '{}')
    3. Hypotheses stuck at 0.3-0.7 confidence for > 2 days (ambiguous)
    4. Active contradictions older than 2 days with severity > 0.5

    Each source is collected inside its own try/except (logged at debug) —
    one failing source (e.g. migration 015 not applied, thread table
    missing) never blanks out the others.

    Args:
        case_id: the case to scan.
        current_turn: conversation turn (reserved for future
            reinforcement bookkeeping; not used by the current queries).
        max_per_source: cap per source (default 6).

    Returns:
        List of candidate dicts with keys: id, content, item_type, source,
        urgency, internal_activation, origin, activated_by.

    Raises:
        DatabaseUnavailableError if the DB pool is None.
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot collect endogenous: no DB pool")

    candidates: list[dict] = []
    now = datetime.now(timezone.utc)

    # Source 1 — aged investigation threads
    try:
        from case_engine.investigation_thread import InvestigationThreadRegistry
        registry = InvestigationThreadRegistry(case_id)
        await registry._ensure_loaded()
        for thread in await registry.get_active_by_debt(limit=max_per_source):
            age_days = (now - thread.last_progress_at).total_seconds() / 86400
            if age_days > _AGED_THREAD_DAYS:
                debt = registry.compute_debt(thread)
                candidates.append({
                    "id": f"endogenous_thread_{thread.thread_id}",
                    "content": thread.question,
                    "item_type": "investigation_thread",
                    "source": "endogenous",
                    "urgency": min(1.0, debt / 5.0),
                    "internal_activation": min(1.0, debt / 5.0),
                    "origin": "endogenous_thread_revisit",
                    "activated_by": "debt_aging",
                })
    except Exception as exc:
        logger.debug("Thread collection failed: %s", exc)

    # Source 2 — uncited evidence
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT evidence_id, content, recorded_at
                FROM evidence
                WHERE case_id = $1
                  AND status = 'ACTIVE'
                  AND extracted_claim_ids = '{{}}'
                  AND recorded_at < NOW() - INTERVAL '{_UNCITED_EVIDENCE_HOURS} hours'
                ORDER BY recorded_at ASC
                LIMIT $2
                """,
                case_id, max_per_source,
            )
        for row in rows:
            age_hours = (now - row["recorded_at"]).total_seconds() / 3600
            urgency = min(1.0, 0.3 + 0.05 * age_hours)
            candidates.append({
                "id": f"endogenous_uncited_{row['evidence_id']}",
                "content": (
                    f"Uncited evidence from {row['recorded_at'].isoformat()}: "
                    f"{row['content'][:200]}"
                ),
                "item_type": "evidence_review",
                "source": "endogenous",
                "urgency": urgency,
                "internal_activation": urgency,
                "origin": "endogenous_uncited_evidence",
                "activated_by": "evidence_not_cited",
            })
    except Exception as exc:
        logger.debug("Uncited evidence collection failed: %s", exc)

    # Source 3 — stuck hypotheses (0.3-0.7 confidence, older than 2 days)
    try:
        from case_engine.hypothesis import list_hypotheses_for_case, get_current_version
        for h in await list_hypotheses_for_case(case_id):
            try:
                v = await get_current_version(h.hypothesis_id)
            except Exception:
                continue
            age_days = (now - v.created_at).total_seconds() / 86400
            if age_days > _STUCK_HYPOTHESIS_DAYS and 0.3 <= v.confidence <= 0.7:
                candidates.append({
                    "id": f"endogenous_stuck_{h.hypothesis_id}",
                    "content": (
                        f"Hypothesis stuck at {v.confidence:.2f} for "
                        f"{age_days:.1f} days: {h.statement}"
                    ),
                    "item_type": "hypothesis_revisit",
                    "source": "endogenous",
                    "urgency": 0.6,
                    "internal_activation": 0.6,
                    "origin": "endogenous_stuck_hypothesis",
                    "activated_by": "stuck_hypothesis",
                })
    except Exception as exc:
        logger.debug("Stuck hypothesis collection failed: %s", exc)

    # Source 4 — aged contradictions
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT contradiction_id, claim_a, claim_b, severity, created_at
                FROM contradictions
                WHERE case_id = $1
                  AND status = 'active'
                  AND severity > 0.5
                  AND created_at < NOW() - INTERVAL '{_AGED_CONTRADICTION_DAYS} days'
                ORDER BY severity DESC, created_at ASC
                LIMIT $2
                """,
                case_id, max_per_source,
            )
        for row in rows:
            candidates.append({
                "id": f"endogenous_contradiction_{row['contradiction_id']}",
                "content": (
                    f"Unreconciled contradiction from {row['created_at'].date()}: "
                    f"{row['claim_a'][:100]} vs {row['claim_b'][:100]}"
                ),
                "item_type": "contradiction_revisit",
                "source": "endogenous",
                "urgency": row["severity"],
                "internal_activation": row["severity"],
                "origin": "endogenous_aged_contradiction",
                "activated_by": "contradiction_aging",
            })
    except Exception as exc:
        logger.debug("Aged contradiction collection failed: %s", exc)

    # ------------------------------------------------------------------
    # Hari ICE "addition and improvements" pass — new candidate sources.
    # The audit's verdict on the original four sources: "a useful
    # foundation, but ... the current mechanism does not independently
    # discover new research directions, unexplained patterns, or
    # surprising connections." These sources close that gap. Each is
    # individually guarded so a missing table / migration never blanks
    # the others.
    # ------------------------------------------------------------------

    # Source 5 — unexplained observations (claims no hypothesis covers).
    # The investigative-curiosity source: material the current explanatory
    # set cannot account for.
    try:
        from case_engine.induction import unexplained_observations
        for u in (await unexplained_observations(case_id))[:max_per_source]:
            candidates.append({
                "id": f"endogenous_unexplained_{u['claim_id']}",
                "content": (
                    "No current hypothesis explains this observation: "
                    f"{u['statement'][:200]}"
                ),
                "item_type": "unexplained_observation",
                "source": "endogenous",
                "urgency": 0.55,
                "internal_activation": 0.55,
                "origin": "endogenous_unexplained_observation",
                "activated_by": "coverage_gap",
            })
    except Exception as exc:
        logger.debug("Unexplained-observation collection failed: %s", exc)

    # Source 6 — zero-evidence hypotheses (explanations that never earned
    # belief: no supporting OR contradicting evidence ever linked).
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT h.hypothesis_id, h.statement
                FROM hypotheses h
                LEFT JOIN hypothesis_versions hv
                  ON hv.version_id = h.current_version_id
                WHERE h.case_id = $1
                  AND h.status NOT IN ('FALSIFIED', 'CONFIRMED')
                  AND NOT EXISTS (
                      SELECT 1 FROM evidence_graph_edges e
                      WHERE e.to_node = h.hypothesis_id
                        AND e.relation IN ('SUPPORTS', 'CONTRADICTS')
                  )
                  AND h.created_at < NOW() - INTERVAL '6 hours'
                ORDER BY h.created_at ASC
                LIMIT $2
                """,
                case_id, max_per_source,
            )
        for row in rows:
            candidates.append({
                "id": f"endogenous_zeroev_{row['hypothesis_id']}",
                "content": (
                    "Hypothesis with no evidence linked yet (untested "
                    f"explanation): {row['statement'][:180]}"
                ),
                "item_type": "zero_evidence_hypothesis",
                "source": "endogenous",
                "urgency": 0.5,
                "internal_activation": 0.5,
                "origin": "endogenous_zero_evidence_hypothesis",
                "activated_by": "untested_explanation",
            })
    except Exception as exc:
        logger.debug("Zero-evidence hypothesis collection failed: %s", exc)

    # Source 7 — dormant questions: unresolved information gaps aging past
    # their expected usefulness (FSRS-inspired resurfacing urgency: grows
    # with age, saturating — an old question deserves a *reconsideration*,
    # not a panic).
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT gap_id, needed_evidence, priority, created_at
                FROM information_gaps
                WHERE case_id = $1
                  AND resolved_at IS NULL
                  AND created_at < NOW() - INTERVAL '1 day'
                ORDER BY priority DESC, created_at ASC
                LIMIT $2
                """,
                case_id, max_per_source,
            )
        now = datetime.now(timezone.utc)
        for row in rows:
            age_days = (now - row["created_at"]).total_seconds() / 86400
            urgency = min(1.0, 0.35 + 0.08 * age_days)
            candidates.append({
                "id": f"endogenous_dormant_{row['gap_id']}",
                "content": (
                    f"Dormant question (open {age_days:.0f}d): "
                    f"{row['needed_evidence'][:180]}"
                ),
                "item_type": "dormant_question",
                "source": "endogenous",
                "urgency": urgency,
                "internal_activation": urgency,
                "origin": "endogenous_dormant_question",
                "activated_by": "question_aging",
            })
    except Exception as exc:
        logger.debug("Dormant-question collection failed: %s", exc)

    # Source 8 — contradiction clusters: one claim tangled in several
    # contradictions (a hub claim may be wrong, or a hidden common cause
    # sits behind the whole cluster — the "hidden common cause" pattern
    # the audit called out).
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT c.claim_id, cl.statement, COUNT(*) AS entanglement
                FROM contradictions c
                JOIN claims cl ON cl.claim_id IN (c.claim_a, c.claim_b)
                WHERE c.case_id = $1 AND c.status = 'active'
                GROUP BY c.claim_id, cl.statement
                HAVING COUNT(*) >= 2
                ORDER BY entanglement DESC
                LIMIT $2
                """,
                case_id, max_per_source,
            )
        for row in rows:
            candidates.append({
                "id": f"endogenous_cluster_{row['claim_id']}",
                "content": (
                    f"Claim entangled in {row['entanglement']} contradictions "
                    f"(possible hidden common cause): {row['statement'][:180]}"
                ),
                "item_type": "contradiction_cluster",
                "source": "endogenous",
                "urgency": min(1.0, 0.4 + 0.15 * int(row["entanglement"])),
                "internal_activation": 0.6,
                "origin": "endogenous_contradiction_cluster",
                "activated_by": "contradiction_hub",
            })
    except Exception as exc:
        logger.debug("Contradiction-cluster collection failed: %s", exc)

    # Source 9 — prediction failures: REFUTED predictions attached to a
    # hypothesis that is still not FALSIFIED (the model expected the world
    # to behave differently — the surprise signal).
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT p.prediction_id, p.statement, p.hypothesis_id,
                       p.observed_result
                FROM hypothesis_predictions p
                JOIN hypotheses h ON h.hypothesis_id = p.hypothesis_id
                WHERE p.case_id = $1
                  AND p.status = 'REFUTED'
                  AND h.status NOT IN ('FALSIFIED')
                ORDER BY p.resolved_at DESC
                LIMIT $2
                """,
                case_id, max_per_source,
            )
        for row in rows:
            candidates.append({
                "id": f"endogenous_predfail_{row['prediction_id']}",
                "content": (
                    "A prediction of this hypothesis was REFUTED but the "
                    f"hypothesis still stands: '{row['statement'][:120]}' "
                    f"(observed: {str(row['observed_result'])[:100]})"
                ),
                "item_type": "prediction_failure",
                "source": "endogenous",
                "urgency": 0.75,
                "internal_activation": 0.75,
                "origin": "endogenous_prediction_failure",
                "activated_by": "prediction_error",
            })
    except Exception as exc:
        logger.debug("Prediction-failure collection failed: %s", exc)

    return candidates


async def collect_global_endogenous_candidates(
    exclude_case_id: Optional[str] = None,
    max_per_source: int = 5,
) -> list[dict]:
    """Cross-case endogenous candidates (global workspace feed).

    Sources:
        1. Cross-case connections — evidence in OTHER cases lexically
           similar to the excluded case's recent evidence ("possible
           connection between two apparently unrelated findings").
        2. Other cases' unresolved-but-stale investigations (aged threads
           across the whole case set — "worth thinking about even when
           nobody has asked about it").

    The cross-case scan is bounded: one lexical pass per anchor, capped
    anchors, capped hits (no O(cases × evidence) blowups).
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot collect global endogenous: no DB pool")

    candidates: list[dict] = []

    # Source 1 — cross-case connections via retrieval
    if exclude_case_id:
        try:
            from case_engine.retrieval import find_cross_case_connections

            connections = await find_cross_case_connections(
                exclude_case_id, limit=max_per_source
            )
            for c in connections:
                candidates.append({
                    "id": (
                        f"global_conn_{c['from_evidence_id']}_{c['to_evidence_id']}"
                    ),
                    "content": (
                        f"Possible cross-case connection (score "
                        f"{c['score']}): this case's evidence resembles "
                        f"evidence in case {c['to_case_id']}: "
                        f"{c['to_content'][:140]}"
                    ),
                    "item_type": "cross_case_connection",
                    "source": "endogenous_global",
                    "urgency": min(0.8, 0.3 + c["score"]),
                    "internal_activation": 0.5,
                    "origin": "endogenous_cross_case",
                    "activated_by": "lexical_similarity",
                })
        except Exception as exc:
            logger.debug("Cross-case connection scan failed: %s", exc)

    # Source 2 — stale unresolved investigations across cases
    try:
        async with pool.acquire() as conn:
            sql = """
                SELECT case_id, title, updated_at
                FROM cases
                WHERE status IN ('open', 'investigating')
            """
            params: list = []
            if exclude_case_id:
                params.append(exclude_case_id)
                sql += " AND case_id <> $1"
            sql += """
                  AND updated_at < NOW() - INTERVAL '3 days'
                ORDER BY updated_at ASC
                LIMIT $2
            """
            params.append(max_per_source)
            rows = await conn.fetch(sql, *params)
        for row in rows:
            candidates.append({
                "id": f"global_stale_case_{row['case_id']}",
                "content": (
                    f"Investigation '{row['title'][:80]}' has been open and "
                    f"untouched since {row['updated_at'].date()} — still worth "
                    "pursuing, or should it be closed?"
                ),
                "item_type": "dormant_question",
                "source": "endogenous_global",
                "urgency": 0.4,
                "internal_activation": 0.4,
                "origin": "endogenous_stale_case",
                "activated_by": "case_aging",
            })
    except Exception as exc:
        logger.debug("Stale-case collection failed: %s", exc)

    return candidates