"""case_engine/decision.py — ACT / INVESTIGATE / ABSTAIN decision loop.

Owner:      ICE revamp, Part 3 §8.
Purpose:    decide(case_id) chooses the next decision type for a case from
            the six-rule ladder, persists the Decision, and emits
            decision_made.
Deps:       db.connection, case_engine (_emit_event), case_engine.exceptions,
            case_engine.schemas, case_engine.case, case_engine.hypothesis,
            case_engine.contradiction, case_engine.action,
            case_engine.next_best_action.

Part 3 §0 rules honored here:
    Rule 11 — decide scores and ranks actions, so it calls
        compute_priority_with_factors and embeds the winning action's full
        factor breakdown into top_factors ("best_action_factors"). Silent
        scoring is forbidden.
    Rule 14 — confidences and priorities used here are planning beliefs in
        [0, 1], not calibrated probabilities.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from db.connection import get_pool
from case_engine import _emit_event
from case_engine.exceptions import (
    CaseNotFoundError,
    DatabaseUnavailableError,
)
from case_engine.schemas import Decision, DecisionType
from case_engine.case import get_case
from case_engine.hypothesis import list_hypotheses_for_case, get_current_version
from case_engine.contradiction import get_active_contradictions
from case_engine.action import generate_actions
from case_engine.next_best_action import compute_priority_with_factors

logger = logging.getLogger(__name__)


def _row_to_decision(row) -> Decision:
    """Explicit row -> Decision mapping (no **row splat; Part 2 §0 rule 8)."""
    # P3/I3 (merge spec Part 1 §5.3): jsonb columns can come back as raw
    # JSON strings (custom codec / JSONB->TEXT migration); parse defensively.
    alts = row["alternatives_considered"]
    if isinstance(alts, str):
        try:
            alts = json.loads(alts)
        except Exception:
            alts = []
    top_f = row["top_factors"]
    if isinstance(top_f, str):
        try:
            top_f = json.loads(top_f)
        except Exception:
            top_f = {}

    return Decision(
        decision_id=row["decision_id"],
        case_id=row["case_id"],
        decision_type=row["decision_type"],
        recommended_action_id=row["recommended_action_id"],
        rationale=row["rationale"],
        confidence=row["confidence"],
        alternatives_considered=list(alts or []),
        top_factors=dict(top_f or {}),
        created_at=row["created_at"],
    )


async def decide(case_id: str) -> Decision:
    """Choose ACT, INVESTIGATE, or ABSTAIN for a case.

    Part 2 §3.2 (Feature 2B): branches on case.case_type —
    - 'debug': 6-rule confidence ladder (_decide_debug, existing behavior)
    - 'research': contradiction + gap based synthesis completeness
      (_decide_research)
    - 'explore': always INVESTIGATE — the user resolves the case manually
      (_decide_explore; documented stub, doc Part 2 §12 shortcut #6)

    Postconditions:
        decisions row created (by the branch's _make_decision call).
        decision_made event emitted.
        Returns the Decision.
    Raises:
        CaseNotFoundError: if the case does not exist.
        DatabaseUnavailableError.
    Side effects:
        INSERT INTO decisions (1).
        _emit_event("decision_made", {...}).
    """
    case = await get_case(case_id)

    if case.case_type == "research":
        return await _decide_research(case)
    if case.case_type == "explore":
        return await _decide_explore(case)
    return await _decide_debug(case_id)


async def _decide_debug(case_id: str) -> Decision:
    """Debug mode: the existing 6-rule confidence ladder, unchanged.

    Rule order (first matching rule wins):
        1. No hypotheses -> ABSTAIN (confidence=1.0).
        2. Leading confidence >= required_confidence AND no decision-critical
           contradictions (severity > 0.7) -> ACT.
        3. Leading confidence >= required_confidence AND decision-critical
           contradictions exist -> INVESTIGATE.
        4. No actionable investigation steps -> ABSTAIN.
        5. Best-action priority < 0.15 -> ABSTAIN.
        6. Otherwise -> INVESTIGATE with the best action as recommendation.

    (Guard between rules 1 and 2: if hypothesis versions cannot be loaded at
    all, ABSTAIN with confidence=0.5 — the spec's "All hypothesis versions
    unavailable" branch.)

    Part 2 §3.2: every _make_decision call now carries
    ``"case_type": "debug"`` in top_factors for cross-mode disambiguation
    (doc §12 shortcut #14: ACT means different things per mode; the
    top_factors field is the disambiguator until case-type-specific decision
    symbols exist).
    """
    case = await get_case(case_id)
    hypotheses = await list_hypotheses_for_case(case_id)

    # Rule 1 — no hypotheses
    if not hypotheses:
        return await _make_decision(
            case_id=case_id,
            decision_type="ABSTAIN",
            recommended_action_id=None,
            rationale="No hypotheses have been proposed yet",
            confidence=1.0,
            alternatives=[],
            top_factors={"reason": "no_hypotheses", "case_type": "debug"},
        )

    leading = None
    leading_conf = -1.0
    for h in hypotheses:
        try:
            v = await get_current_version(h.hypothesis_id)
        except Exception:
            continue
        if v.confidence > leading_conf:
            leading_conf = v.confidence
            leading = h

    # Guard — versions unavailable
    if leading is None:
        return await _make_decision(
            case_id=case_id,
            decision_type="ABSTAIN",
            recommended_action_id=None,
            rationale="All hypothesis versions unavailable",
            confidence=0.5,
            alternatives=[],
            top_factors={"reason": "no_leading_hypothesis", "case_type": "debug"},
        )

    # Rules 2 + 3 — confidence threshold reached
    if leading_conf >= case.required_confidence:
        contradictions = await get_active_contradictions(case_id)
        decision_critical = [c for c in contradictions if c.severity > 0.7]
        if decision_critical:
            return await _make_decision(
                case_id=case_id,
                decision_type="INVESTIGATE",
                recommended_action_id=None,
                rationale=(
                    f"Confidence is sufficient but {len(decision_critical)} "
                    f"decision-critical contradictions remain unresolved"
                ),
                confidence=leading_conf,
                alternatives=[],
                top_factors={
                    "reason": "unresolved_contradictions",
                    "contradiction_count": len(decision_critical),
                    "case_type": "debug",
                },
            )
        return await _make_decision(
            case_id=case_id,
            decision_type="ACT",
            recommended_action_id=None,
            rationale=(
                f"Leading hypothesis '{leading.statement[:80]}' at "
                f"{leading_conf:.2f} exceeds required {case.required_confidence:.2f}"
            ),
            confidence=leading_conf,
            alternatives=[],
            top_factors={"leading_confidence": leading_conf, "case_type": "debug"},
        )

    # Need more investigation
    actions = await generate_actions(case_id)
    if not actions:
        return await _make_decision(
            case_id=case_id,
            decision_type="ABSTAIN",
            recommended_action_id=None,
            rationale="No actionable investigation steps are available",
            confidence=leading_conf,
            alternatives=[],
            top_factors={"reason": "no_actions_available", "case_type": "debug"},
        )

    # Rule 11 compliance: score with the full factor breakdown attached.
    scored: list[tuple[float, object, dict]] = []
    for action in actions:
        try:
            p, factors = await compute_priority_with_factors(action)
        except Exception as exc:
            logger.debug("Priority failed: %s", exc)
            continue
        scored.append((p, action, factors))

    if not scored:
        return await _make_decision(
            case_id=case_id,
            decision_type="ABSTAIN",
            recommended_action_id=None,
            rationale="All action priorities failed to compute",
            confidence=leading_conf,
            alternatives=[],
            top_factors={"reason": "priority_computation_failed", "case_type": "debug"},
        )

    scored.sort(key=lambda x: x[0], reverse=True)
    best_priority, best_action, best_factors = scored[0]

    # Rule 5 — below actionability threshold
    if best_priority < 0.15:
        return await _make_decision(
            case_id=case_id,
            decision_type="ABSTAIN",
            recommended_action_id=None,
            rationale=(
                f"Best action priority {best_priority:.2f} is below actionability "
                f"threshold. Remaining uncertainty cannot be resolved cost-effectively."
            ),
            confidence=leading_conf,
            alternatives=[
                {
                    "action_id": a.action_id,
                    "action_type": a.action_type,
                    "priority": p,
                }
                for p, a, _f in scored[1:4]
            ],
            top_factors={
                "best_priority": best_priority,
                "best_action_factors": best_factors,
                "case_type": "debug",
            },
        )

    # Rule 6 — investigate with a recommendation
    return await _make_decision(
        case_id=case_id,
        decision_type="INVESTIGATE",
        recommended_action_id=best_action.action_id,
        rationale=(
            f"Recommended action: {best_action.action_type} on "
            f"'{best_action.target or 'unspecified'}' (priority {best_priority:.2f})"
        ),
        confidence=leading_conf,
        alternatives=[
            {
                "action_id": a.action_id,
                "action_type": a.action_type,
                "priority": p,
            }
            for p, a, _f in scored[1:4]
        ],
        top_factors={
            "best_priority": best_priority,
            "leading_confidence": leading_conf,
            "gap_to_threshold": case.required_confidence - leading_conf,
            "best_action_factors": best_factors,
            "case_type": "debug",
        },
    )


async def _decide_research(case) -> Decision:
    """Research-mode decision logic (Part 2 §3.2, Feature 2B).

    Rules (first matching wins):
    1. No hypotheses -> INVESTIGATE (need at least one synthesis direction)
    2. Any active contradictions -> INVESTIGATE (reconcile first)
    3. Any gap with priority > 0.5 -> INVESTIGATE (fill high-value gaps)
    4. Otherwise -> ACT (ready to synthesize)

    HONEST NOTE (doc Part 2 §12 shortcut #5): the 0.5 "high-priority gap"
    threshold is arbitrary — the principled version would derive it from
    the user's stated goal urgency and the gap-distribution shape.
    HONEST NOTE (doc Part 2 §12 shortcut #14): 'ACT' here means "ready to
    synthesize", not debug-mode's "root cause found, execute the fix" —
    the same decision_type symbol carries different semantics per mode;
    top_factors["case_type"] disambiguates.
    """
    from case_engine.information_gap import list_gaps_for_case

    case_id = case.case_id
    hypotheses = await list_hypotheses_for_case(case_id)

    # Rule 1
    if not hypotheses:
        return await _make_decision(
            case_id=case_id,
            decision_type="INVESTIGATE",
            recommended_action_id=None,
            rationale="No synthesis hypotheses yet; propose at least one",
            confidence=0.0,
            alternatives=[],
            top_factors={"reason": "no_hypotheses", "case_type": "research"},
        )

    # Rule 2
    contradictions = await get_active_contradictions(case_id)
    if contradictions:
        return await _make_decision(
            case_id=case_id,
            decision_type="INVESTIGATE",
            recommended_action_id=None,
            rationale=f"{len(contradictions)} active contradictions need reconciliation",
            confidence=0.0,
            alternatives=[],
            top_factors={
                "reason": "active_contradictions",
                "contradiction_count": len(contradictions),
                "case_type": "research",
            },
        )

    # Rule 3
    gaps = await list_gaps_for_case(case_id, resolved=False)
    high_priority_gaps = [g for g in gaps if g.priority > 0.5]
    if high_priority_gaps:
        return await _make_decision(
            case_id=case_id,
            decision_type="INVESTIGATE",
            recommended_action_id=None,
            rationale=f"{len(high_priority_gaps)} high-priority gaps still unresolved",
            confidence=0.0,
            alternatives=[],
            top_factors={
                "reason": "high_priority_gaps",
                "gap_count": len(high_priority_gaps),
                "case_type": "research",
            },
        )

    # Rule 4
    return await _make_decision(
        case_id=case_id,
        decision_type="ACT",
        recommended_action_id=None,
        rationale="No active contradictions, no high-priority gaps. Ready to synthesize.",
        confidence=1.0,
        alternatives=[],
        top_factors={"reason": "ready_to_synthesize", "case_type": "research"},
    )


async def _decide_explore(case) -> Decision:
    """Explore mode: the REAL decision ladder (formerly a documented stub —
    Part 2 §12 shortcut #6, completed by the Hari ICE improvements pass).

    The audit's words: "The name promises a capability the implementation
    does not deliver." Now it delivers:

    Rule 1 — no hypotheses AND no workspace candidates -> INVESTIGATE
        ("propose a direction"; the induction engine is the natural next
        step, surfaced in the rationale).
    Rule 2 — the workspace has high-value exploration/speculation
        candidates -> INVESTIGATE with the top candidate named (ambient
        exploration is the point of explore mode).
    Rule 3 — every hypothesis is stale (no evidence events recently, no
        workspace candidates) -> ABSTAIN with an explicit "consider
        resolving or archiving" rationale (explore mode must be allowed to
        notice its own dead ends instead of looping INVESTIGATE forever).
    Rule 4 — default -> INVESTIGATE (user drives resolution; system
        suggests, never auto-acts).

    The system still never auto-acts in explore mode; the user resolves
    the case manually via update_case_status.
    """
    case_id = case.case_id
    hypotheses = await list_hypotheses_for_case(case_id)

    # Workspace candidates (best-effort: the workspace module is new and
    # its tables may be mid-migration — a broken workspace must not break
    # decisions).
    workspace_top = None
    try:
        from case_engine.workspace import run_workspace_cycle

        ws = await run_workspace_cycle(case_id, budget=5)
        selected = ws.get("selected") or []
        if selected:
            workspace_top = selected[0]
    except Exception as exc:
        logger.debug("workspace pass in explore ladder failed: %s", exc)

    # Rule 1 — no direction at all
    if not hypotheses and workspace_top is None:
        return await _make_decision(
            case_id=case_id,
            decision_type="INVESTIGATE",
            recommended_action_id=None,
            rationale=(
                "Explore mode: no direction yet. Run hypothesis induction "
                "(case_engine.induction) or ingest more material; nothing "
                "is currently worth acting on."
            ),
            confidence=0.0,
            alternatives=[],
            top_factors={"reason": "no_direction", "case_type": "explore"},
        )

    # Rule 2 — a high-value exploration/speculation candidate exists
    if workspace_top is not None and workspace_top.get("score", 0.0) >= 0.35:
        return await _make_decision(
            case_id=case_id,
            decision_type="INVESTIGATE",
            recommended_action_id=None,
            rationale=(
                f"Explore mode: workspace surfaced a "
                f"{workspace_top.get('category')} candidate "
                f"(score {workspace_top.get('score', 0):.2f}): "
                f"{workspace_top.get('content', '')[:140]}"
            ),
            confidence=float(workspace_top.get("score", 0.5)),
            alternatives=[],
            top_factors={
                "reason": "workspace_candidate",
                "candidate_key": workspace_top.get("candidate_key"),
                "category": workspace_top.get("category"),
                "factors": workspace_top.get("factors"),
                "case_type": "explore",
            },
        )

    # Rule 3 — stale: hypotheses exist but nothing new is arriving
    if hypotheses and workspace_top is None:
        pool = await get_pool()
        recent_events = 0
        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    recent_events = await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM case_events
                        WHERE case_id = $1
                          AND event_type IN ('evidence_ingested',
                                             'hypothesis_version_created')
                          AND wall_clock_time > NOW() - INTERVAL '7 days'
                        """,
                        case_id,
                    ) or 0
            except Exception:
                recent_events = 1  # unknown -> do not claim staleness
        if recent_events == 0:
            return await _make_decision(
                case_id=case_id,
                decision_type="ABSTAIN",
                recommended_action_id=None,
                rationale=(
                    "Explore mode: no new evidence or hypothesis movement "
                    "for 7 days and no workspace candidates. Consider "
                    "resolving, archiving, or deliberately reopening this "
                    "case instead of leaving it half-explored."
                ),
                confidence=0.5,
                alternatives=[],
                top_factors={
                    "reason": "stale_exploration",
                    "hypothesis_count": len(hypotheses),
                    "case_type": "explore",
                },
            )

    # Rule 4 — default
    return await _make_decision(
        case_id=case_id,
        decision_type="INVESTIGATE",
        recommended_action_id=None,
        rationale=(
            "Explore mode: user drives resolution; system suggests next "
            "steps" + (
                f" (top workspace candidate: {workspace_top['content'][:100]})"
                if workspace_top else ""
            )
        ),
        confidence=0.5,
        alternatives=[],
        top_factors={
            "reason": "explore_mode",
            "workspace_top": (
                workspace_top.get("candidate_key") if workspace_top else None
            ),
            "case_type": "explore",
        },
    )


async def _make_decision(
    case_id: str,
    decision_type: DecisionType,
    recommended_action_id: Optional[str],
    rationale: str,
    confidence: float,
    alternatives: list[dict],
    top_factors: dict,
) -> Decision:
    """Internal helper: persist + emit + return a Decision.

    Side effects:
        INSERT INTO decisions (1).
        _emit_event("decision_made", {decision_id, decision_type, confidence}).
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot persist decision: no DB pool")

    decision_id = f"dec_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO decisions (
                decision_id, case_id, decision_type, recommended_action_id,
                rationale, confidence, alternatives_considered, top_factors, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9)
            """,
            decision_id, case_id, decision_type, recommended_action_id,
            rationale, confidence,
            json.dumps(alternatives), json.dumps(top_factors), now,
        )

    await _emit_event(case_id, "decision_made", {
        "decision_id": decision_id,
        "decision_type": decision_type,
        "confidence": confidence,
    })

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM decisions WHERE decision_id = $1", decision_id
        )
    return _row_to_decision(row)