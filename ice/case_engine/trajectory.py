"""case_engine/trajectory.py — AgentRx-lite failure localization.

Owner:      Hari ICE "addition and improvements" pass. Adapts the AgentRx
            idea (diagnose WHERE in a long trajectory the agent first went
            wrong, rather than grading only the final answer) to ICE's
            event log, with rule-based invariants instead of an LLM judge:
            "You need to locate the first meaningful failure, not merely
            say that the final hypothesis was wrong."
Invariants checked over the ordered case_events sequence:
    V1  an action executed without a preceding action_approved event for it
    V2  a hypothesis confidence ROSE with no new evidence_ingested /
        claim_extracted / action_executed-with-evidence event since the
        last version (belief without observation)
    V3  a contradiction detected with no follow-up action within N events
        (a contradiction nobody investigates)
    V4  an evidence-expecting action completed with zero result evidence
        (activity masquerading as progress — should be extinct after the
        P1 outcome semantics, so a hit indicates an injected/legacy
        executor or a bug)
    V5  a decision_made with decision_type ACT while decision-critical
        contradictions were active
    V6  budget exhaustion mid-cycle (actions proposed == budget while the
        case remains unresolved)
Classification vocabulary (AgentRx-aligned): tool_contract_violation,
ignored_instruction, misinterpreted_result, unsupported_action,
infrastructure_failure — mapped onto ICE's own analogue of each.
Deps:       db.connection, case_engine.exceptions (read-only).
"""

from __future__ import annotations

import logging

from db.connection import get_pool
from case_engine.exceptions import DatabaseUnavailableError

logger = logging.getLogger(__name__)

# how many later events a contradiction has to produce a proposed action in
_CONTRA_FOLLOWUP_WINDOW = 8


async def localize_first_failure(case_id: str) -> dict:
    """Scan one case's trajectory; return the FIRST meaningful failure.

    Returns:
        {"first_failure": {...} | None, "violations": [...], "scanned": n}
    Violations are ordered by event_seq; the first one is the answer —
    everything after it is downstream noise.
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")

    async with pool.acquire() as conn:
        events = await conn.fetch(
            """
            SELECT event_seq, event_type, payload, wall_clock_time
            FROM case_events WHERE case_id = $1 ORDER BY event_seq
            """,
            case_id,
        )
        budget = await conn.fetchrow(
            "SELECT cost_budget_tool_calls, status, required_confidence "
            "FROM cases WHERE case_id = $1", case_id,
        )
        action_rows = await conn.fetch(
            """
            SELECT action_id, action_type, status, result_evidence_ids,
                   outcome, created_at
            FROM actions WHERE case_id = $1
            """,
            case_id,
        )

    import json as _json

    def payload_of(e):
        raw = e["payload"]
        if isinstance(raw, dict):
            return raw
        try:
            return _json.loads(raw) if raw else {}
        except Exception:
            return {}

    violations: list[dict] = []
    approved_actions: set[str] = set()
    executed_actions: set[str] = set()
    last_evidence_seq = 0
    open_contradictions: dict[str, int] = {}  # id -> seq detected

    action_info = {r["action_id"]: dict(r) for r in action_rows}

    for e in events:
        seq = e["event_seq"]
        etype = e["event_type"]
        payload = payload_of(e)

        if etype == "action_approved":
            approved_actions.add(payload.get("action_id"))
        elif etype == "action_executed":
            aid = payload.get("action_id")
            # V1: executed without approval
            if aid and aid not in approved_actions and aid not in executed_actions:
                violations.append({
                    "seq": seq,
                    "invariant": "V1_execution_without_approval",
                    "classification": "tool_contract_violation",
                    "detail": f"action {aid} executed with no action_approved event",
                })
            if aid:
                executed_actions.add(aid)
            # V4: evidence-expecting action completed with zero evidence
            info = action_info.get(aid)
            if (
                info
                and info.get("status") == "completed"
                and not (info.get("result_evidence_ids") or [])
                and info.get("action_type")
                in ("SEARCH", "RETRIEVE", "INSPECT", "RUN_TEST",
                    "COMPARE", "FIND_COUNTEREVIDENCE")
                and info.get("outcome") in (None, "EVIDENCE_PRODUCED")
            ):
                violations.append({
                    "seq": seq,
                    "invariant": "V4_completed_without_evidence",
                    "classification": "misinterpreted_result",
                    "detail": (
                        f"action {aid} ({info['action_type']}) completed "
                        "with zero result evidence"
                    ),
                })
        elif etype in ("evidence_ingested", "claim_extracted"):
            last_evidence_seq = seq
        elif etype == "hypothesis_version_created":
            # V2: confidence rose without any new observation
            prior = payload.get("prior_confidence")
            new = payload.get("new_confidence")
            if (
                isinstance(prior, (int, float))
                and isinstance(new, (int, float))
                and new > prior + 0.01
                and last_evidence_seq == 0
            ):
                violations.append({
                    "seq": seq,
                    "invariant": "V2_belief_without_observation",
                    "classification": "ignored_instruction",
                    "detail": (
                        f"hypothesis {payload.get('hypothesis_id')} rose "
                        f"{prior:.2f}->{new:.2f} with no evidence event "
                        "ever recorded in the case"
                    ),
                })
        elif etype == "contradiction_detected":
            open_contradictions[payload.get("contradiction_id")] = seq
        elif etype == "contradiction_resolved":
            open_contradictions.pop(payload.get("contradiction_id"), None)
        elif etype == "action_proposed":
            # V3 follow-up check for contradictions older than the window
            for cid, detected_seq in list(open_contradictions.items()):
                if seq - detected_seq > _CONTRA_FOLLOWUP_WINDOW:
                    violations.append({
                        "seq": seq,
                        "invariant": "V3_contradiction_without_followup",
                        "classification": "ignored_instruction",
                        "detail": (
                            f"contradiction {cid} detected at seq "
                            f"{detected_seq} still has no proposed action "
                            f"{_CONTRA_FOLLOWUP_WINDOW} events later"
                        ),
                })
                    open_contradictions.pop(cid)
        elif etype == "decision_made":
            if payload.get("decision_type") == "ACT" and open_contradictions:
                violations.append({
                    "seq": seq,
                    "invariant": "V5_act_with_open_contradictions",
                    "classification": "misinterpreted_result",
                    "detail": (
                        f"ACT decision with {len(open_contradictions)} open "
                        "contradiction(s)"
                    ),
                })

    # V6: budget exhaustion while unresolved
    if budget:
        proposed_total = len(action_rows)
        if (
            budget["status"] in ("open", "investigating")
            and proposed_total >= int(budget["cost_budget_tool_calls"])
        ):
            violations.append({
                "seq": events[-1]["event_seq"] if events else 0,
                "invariant": "V6_budget_exhausted_unresolved",
                "classification": "infrastructure_failure",
                "detail": (
                    f"{proposed_total} actions proposed >= budget "
                    f"{budget['cost_budget_tool_calls']} while case remains "
                    f"{budget['status']}"
                ),
            })

    violations.sort(key=lambda v: v["seq"])
    return {
        "case_id": case_id,
        "first_failure": violations[0] if violations else None,
        "violations": violations,
        "scanned_events": len(events),
        "note": (
            "First meaningful failure localization (AgentRx-adapted): "
            "everything after the first violation is downstream."
        ),
    }
