"""case_engine/cycle.py — shared investigation cycle (audit gap #22).

Owner:      ICE revamp, audit gap #22 (PDF page 196's 11-step loop, shared by
            API + CLI instead of the inlined route logic).
Purpose:    run_investigation_cycle(case_id) executes one full investigation
            cycle: snapshot -> recompute all hypotheses -> (optional)
            falsification + action generation -> decide -> gather diffs ->
            recommend the next best action. Returns a dict shaped like the
            API's InvestigationResult so the FastAPI route and the CLI can
            both delegate here.
Deps:       db.connection, case_engine.case, case_engine.hypothesis,
            case_engine.contradiction, case_engine.information_gap,
            case_engine.action, case_engine.decision,
            case_engine.next_best_action, case_engine.falsification.
            Like replay.py, this module sits at the top of the dependency
            graph — nothing inside case_engine may import it.

Renamed from case_engine/investigation.py to match the Oct 23 codebase's
naming (merge spec Part 3 §0.6 Option B). The CONTENT is Oct 22's
(bug-free) version: Oct 23's cycle.py shipped three latent bugs (dead
force_recompute, select_next_best_action not gated under
enable_next_best_action, COUNT(*) race for events_emitted — the last one
fixed here as merge spec Part 1 §6.2 S2). case_engine/investigation.py is
retained as a re-export shim for backward compatibility.
"""

import logging
import time

from db.connection import get_pool
from case_engine.exceptions import DatabaseUnavailableError
from case_engine.case import get_case
from case_engine.hypothesis import (
    list_hypotheses_for_case,
    get_current_version,
    recompute_hypothesis_confidence,
    update_hypothesis_lifecycle,
)
from case_engine.contradiction import get_active_contradictions
from case_engine.information_gap import list_gaps_for_case
from case_engine.action import generate_actions
from case_engine.decision import decide
from case_engine.falsification import run_falsification_loop
from case_engine.next_best_action import select_next_best_action

logger = logging.getLogger(__name__)


async def run_investigation_cycle(
    case_id: str,
    enable_next_best_action: bool = True,
    enable_falsification: bool = False,
    enable_auto_execute: bool = False,
    enable_induction: bool = False,
    enable_workspace: bool = False,
) -> dict:
    """Run one full investigation cycle for a case.

    Steps (mirrors Part 4 §13.6 investigate_route, extracted so the CLI and
    API share one implementation):
        1. Snapshot the case's MAX(event_seq) (for events_emitted).
        2. Snapshot every hypothesis's (confidence, version_number, statement).
        3. Recompute all hypotheses with cause_event_id="investigate".
        3b. Run update_hypothesis_lifecycle for every hypothesis and collect
            the transitions (Part 2 §7.2, fix 2F — hypothesis status used
            to never leave PROPOSED).
        4. Optionally (enable_falsification) run the falsification loop on
           the top-confidence hypothesis and collect its FIND_COUNTEREVIDENCE
           actions. Note: if ENABLE_FALSIFICATION_LOOP is also enabled,
           generate_actions' source 2 contributes additional falsification
           actions independently.
        5. Optionally (enable_next_best_action) generate candidate actions.
        6. decide(case_id) — persists a Decision + decision_made event.
        7. Gather hypothesis_changes (only hypotheses whose confidence moved
           by > 0.01 or whose version_number changed).
        8. Gather active contradictions (ids) — resolved_contradictions is
           always [] (matching the API route shape).
        9. Gather unresolved gaps (ids).
        10. Identify the leading hypothesis.
        11. select_next_best_action(case_id) for the recommendation.
        11b. Optionally (enable_auto_execute, Part 2 §4.4) approve + execute
            the top-priority action and record it as auto_executed_action.
        12. Count case_events with event_seq > before_seq;
            events_emitted = that count (merge spec Part 1 §6.2 S2: the old
            COUNT(*)-before/after diff double-counted events from any cycle
            running concurrently on the same case; event_seq is monotonic
            per case, so this count is exact even under interleaving).

    Args:
        case_id: the case to advance.
        enable_next_best_action: whether to run generate_actions first.
        enable_falsification: whether to run the falsification loop on the
            top-confidence hypothesis (still subject to the
            ENABLE_FALSIFICATION_LOOP env gate inside the falsification
            module).
        enable_auto_execute: whether to approve and execute the
            top-priority action at the end of the cycle (Part 2 §4.4,
            Feature 2C). Default False — the operator stays in the loop
            unless they opt in.
        enable_induction: (Hari ICE improvements pass, P2) run the
            hypothesis induction engine when the case is explanation-
            starved — the cycle can finally GENERATE the explanations it
            later ranks, not merely recompute them. Default False for
            backward compatibility; the no-leakage benchmark and the CLI
            opt in.
        enable_workspace: (P5) run one investigative-workspace pass
            (attention competition + portfolio allocation) and include
            its selected candidates in the result.

    Returns:
        A dict shaped like the API InvestigationResult:
        {case_id, leading_hypothesis, hypothesis_changes,
         new_contradictions, resolved_contradictions, new_gaps, new_actions,
         recommended_action, decision (model_dump), lifecycle_changes,
         auto_executed_action (model_dump mode="json" or None),
         induction (dict or None), workspace (dict or None),
         events_emitted, latency_seconds}.

    Raises:
        CaseNotFoundError: if the case does not exist.
        DatabaseUnavailableError: if the DB pool is None.
    """
    start = time.time()

    # 0 — validate the case exists (raises CaseNotFoundError otherwise).
    await get_case(case_id)

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot run investigation cycle: no DB pool")

    async with pool.acquire() as conn:
        # S2 (merge spec Part 1 §6.2): MAX(event_seq) is monotonic per case,
        # so the end-of-cycle count below (event_seq > before_seq) captures
        # exactly this cycle's events even under concurrent cycles.
        before_seq = await conn.fetchval(
            "SELECT COALESCE(MAX(event_seq), 0) FROM case_events WHERE case_id = $1",
            case_id,
        )

    # 1 — snapshot hypothesis confidences before the recompute.
    hypotheses_before: dict[str, tuple[float, int, str]] = {}
    for h in await list_hypotheses_for_case(case_id):
        try:
            v = await get_current_version(h.hypothesis_id)
            hypotheses_before[h.hypothesis_id] = (v.confidence, v.version_number, h.statement)
        except Exception:
            pass

    # 2 — recompute all hypotheses.
    for h in await list_hypotheses_for_case(case_id):
        try:
            await recompute_hypothesis_confidence(h.hypothesis_id, cause_event_id="investigate")
        except Exception as exc:
            logger.warning(
                "Recompute failed for hypothesis %s: %s", h.hypothesis_id, exc
            )

    # 2b — lifecycle transitions (Part 2 §7.2, fix 2F). Run for every
    # hypothesis; each transition is individually guarded so one bad
    # hypothesis never aborts the cycle.
    lifecycle_changes: list[dict] = []
    for h in await list_hypotheses_for_case(case_id):
        try:
            new_status = await update_hypothesis_lifecycle(h.hypothesis_id, current_turn=0)
            if new_status:
                lifecycle_changes.append({
                    "hypothesis_id": h.hypothesis_id,
                    "new_status": new_status,
                })
        except Exception as exc:
            logger.warning(
                "Lifecycle transition failed for %s: %s", h.hypothesis_id, exc
            )

    # 2c — hypothesis induction (optional, Hari ICE improvements pass).
    # The P2 reasoning-loop completion: the cycle GENERATES competing
    # explanations when the case is explanation-starved, instead of only
    # recomputing hypotheses someone else supplied. Gated off by default
    # (API compatibility); the no-leakage benchmark turns it on.
    induction_result: dict | None = None
    if enable_induction:
        try:
            from case_engine.induction import induce_hypotheses

            induction_result = await induce_hypotheses(case_id, create=True)
        except Exception as exc:
            logger.warning("Induction stage failed: %s", exc)
            induction_result = {"error": str(exc)}

    # 2d — investigative workspace pass (optional, P5): attention
    # competition + portfolio allocation. Selected candidates are included
    # in the result; in explore/research cases they can become
    # REVISIT_HYPOTHESIS actions via generate_actions' existing path.
    workspace_result: dict | None = None
    if enable_workspace:
        try:
            from case_engine.workspace import run_workspace_cycle

            workspace_result = await run_workspace_cycle(case_id, budget=7)
        except Exception as exc:
            logger.warning("Workspace stage failed: %s", exc)
            workspace_result = {"error": str(exc)}

    # 3 — falsification loop (optional).
    new_actions: list = []
    if enable_falsification:
        try:
            top = None
            top_conf = -1.0
            for h in await list_hypotheses_for_case(case_id):
                try:
                    v = await get_current_version(h.hypothesis_id)
                except Exception:
                    continue
                if v.confidence > top_conf:
                    top_conf = v.confidence
                    top = h
            if top is not None:
                new_actions.extend(await run_falsification_loop(top.hypothesis_id))
        except Exception as exc:
            logger.warning("Falsification loop failed: %s", exc)

    # 4 — generate candidate actions (optional).
    if enable_next_best_action:
        try:
            new_actions.extend(await generate_actions(case_id))
        except Exception as exc:
            logger.warning("Action generation failed: %s", exc)

    # 5 — decide.
    decision = await decide(case_id)

    # 6 — gather hypothesis changes (only changed ones).
    changes: list[dict] = []
    for h in await list_hypotheses_for_case(case_id):
        try:
            v = await get_current_version(h.hypothesis_id)
        except Exception:
            continue
        prior = hypotheses_before.get(h.hypothesis_id)
        if (
            prior is None
            or abs(prior[0] - v.confidence) > 0.01
            or prior[1] != v.version_number
        ):
            changes.append({
                "hypothesis_id": h.hypothesis_id,
                "statement": h.statement,
                "prior_confidence": prior[0] if prior else None,
                "current_confidence": v.confidence,
                "version_number": v.version_number,
                "cause_event_id": v.cause_event_id,
            })

    # 7 — active contradictions (ids).
    contradictions = await get_active_contradictions(case_id)
    new_contradictions = [c.contradiction_id for c in contradictions]

    # 8 — unresolved gaps (ids).
    gaps = await list_gaps_for_case(case_id, resolved=False)
    new_gaps = [g.gap_id for g in gaps]

    # 9 — leading hypothesis.
    leading: dict | None = None
    leading_conf = -1.0
    for h in await list_hypotheses_for_case(case_id):
        try:
            v = await get_current_version(h.hypothesis_id)
            if v.confidence > leading_conf:
                leading_conf = v.confidence
                leading = {
                    "hypothesis_id": h.hypothesis_id,
                    "statement": h.statement,
                    "confidence": v.confidence,
                    "version_number": v.version_number,
                }
        except Exception:
            pass

    # 10 — next best action.
    nba = await select_next_best_action(case_id)
    recommended: dict | None = None
    if nba is not None:
        action, priority = nba
        recommended = {
            "action_id": action.action_id,
            "action_type": action.action_type,
            "target": action.target,
            "priority": priority,
        }

    # 10b — auto-execute the top-priority action if requested (Part 2
    # §4.4, Feature 2C). Opt-in: default False keeps the operator in the
    # loop. Failures are logged, never fatal to the cycle.
    auto_executed_action = None
    if enable_auto_execute:
        from case_engine.action import (
            approve_action as _approve,
            execute_action as _execute,
        )
        nba_result = await select_next_best_action(case_id)
        if nba_result is not None:
            top_action, _priority = nba_result
            try:
                await _approve(top_action.action_id)
                auto_executed_action = await _execute(top_action.action_id)
                logger.info(
                    "Auto-executed action %s (%s) → %d new evidence items",
                    top_action.action_id, top_action.action_type,
                    len(auto_executed_action.result_evidence_ids),
                )
            except Exception as exc:
                logger.warning("Auto-execute failed for %s: %s", top_action.action_id, exc)

    async with pool.acquire() as conn:
        events_this_cycle = await conn.fetchval(
            "SELECT COUNT(*) FROM case_events WHERE case_id = $1 AND event_seq > $2",
            case_id, before_seq,
        )

    return {
        "case_id": case_id,
        "leading_hypothesis": leading,
        "hypothesis_changes": changes,
        "new_contradictions": new_contradictions,
        "resolved_contradictions": [],
        "new_gaps": new_gaps,
        "new_actions": [a.action_id for a in new_actions],
        "recommended_action": recommended,
        "decision": decision.model_dump(),
        "lifecycle_changes": lifecycle_changes,
        "auto_executed_action": (
            auto_executed_action.model_dump(mode="json")
            if auto_executed_action else None
        ),
        "induction": induction_result,
        "workspace": workspace_result,
        "events_emitted": events_this_cycle or 0,
        "latency_seconds": time.time() - start,
    }