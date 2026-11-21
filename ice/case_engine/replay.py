"""case_engine/replay.py — event-sourced case state reconstruction.

Owner:      ICE revamp, Part 3 §10 (plus audit gap fixes #8 and #9).
Purpose:    Rebuild case state purely from case_events (replay_case),
            run counterfactual replays excluding one evidence item, measure
            evidence causal influence, and load full decision attributions.
Deps:       db.connection, case_engine.exceptions. Deliberately has no
            case_engine-internal dependencies: replay sits at the top of the
            dependency graph so it can never be the cause of a cycle.

Note: the spec's §10.1 import block lists hypothesis/contradiction helpers
that the replay algorithms never use; they are omitted here (kept out to
avoid dead imports — behavior is unchanged).
"""

import json
import logging
import math
import os
from dataclasses import dataclass, field
from typing import Optional

from db.connection import get_pool
from case_engine.exceptions import DatabaseUnavailableError

logger = logging.getLogger(__name__)


@dataclass
class CaseState:
    """Reconstructed case state (used by replay).

    Fields (Part 3 §10.2, extended per audit gap #9 so the Case Recovery
    metric — active hypotheses, unresolved questions, contradictions, most
    important evidence, latest state changes — can be measured from a
    replay alone):
        hypotheses:       hypothesis_id -> {"statement", "confidence"}
        contradictions:   list of {"contradiction_id", "claim_a", "claim_b",
                          "confidence"}
        evidence_ids:     every evidence_id ingested (in event order)
        event_count:      number of events applied
        unresolved_gaps:  gap_ids created but not yet resolved
        top_evidence_ids: most important replayed evidence (by quality_score,
                          populated by replay_case / replay_without_evidence;
                          empty when unenriched)
        state_changes:    hypothesis lifecycle transitions
                          ({"hypothesis_id", "old_status", "new_status"})
    """

    case_id: str
    hypotheses: dict[str, dict] = field(default_factory=dict)
    contradictions: list[dict] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    event_count: int = 0
    unresolved_gaps: list[str] = field(default_factory=list)
    top_evidence_ids: list[str] = field(default_factory=list)
    state_changes: list[dict] = field(default_factory=list)
    # Hari ICE improvements pass — the audit's "many event types are
    # ignored" recovery gap: claims, actions, decisions, threads and
    # induction runs are now reconstructed too (counts + ids; enough for
    # Case Recovery and trajectory analysis without a full row mirror).
    claims: list[str] = field(default_factory=list)
    actions: dict[str, str] = field(default_factory=dict)  # id -> last status
    decisions: list[dict] = field(default_factory=list)
    threads: list[str] = field(default_factory=list)
    inductions: list[dict] = field(default_factory=list)

    @classmethod
    def empty(cls, case_id: str) -> "CaseState":
        """Return a fresh, empty state for a case (audit gap #8)."""
        return cls(case_id=case_id)

    @property
    def leading_hypothesis_id(self) -> Optional[str]:
        """Highest-confidence hypothesis id, or None when there are none."""
        if not self.hypotheses:
            return None
        return max(self.hypotheses.items(), key=lambda kv: kv[1]["confidence"])[0]

    @property
    def leading_hypothesis_confidence(self) -> float:
        """Confidence of the leading hypothesis (0.0 when there are none)."""
        lid = self.leading_hypothesis_id
        if lid is None:
            return 0.0
        return self.hypotheses[lid]["confidence"]


async def _get_events(case_id: str, up_to_seq: Optional[int] = None) -> list[dict]:
    """Load events for a case, optionally up to a sequence number (ASC).

    Raises: DatabaseUnavailableError if the DB pool is None.
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot load events: no DB pool")
    async with pool.acquire() as conn:
        if up_to_seq is None:
            rows = await conn.fetch(
                "SELECT * FROM case_events WHERE case_id = $1 ORDER BY event_seq ASC",
                case_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM case_events WHERE case_id = $1 AND event_seq <= $2 "
                "ORDER BY event_seq ASC",
                case_id, up_to_seq,
            )
    return [
        {
            "event_id": r["event_id"],
            "event_type": r["event_type"],
            "event_seq": r["event_seq"],
            # P3/I3 (merge spec Part 1 §5.3, Manual Patch 1 loc. 2): jsonb
            # payload may arrive as a raw JSON string depending on codec —
            # parse defensively instead of crashing ({} on garbage).
            "payload": _parse_jsonb_or_empty(r["payload"]),
            "wall_clock_time": r["wall_clock_time"],
        }
        for r in rows
    ]


def _parse_jsonb_or_empty(raw):
    """Defensively decode a jsonb column (merge spec Part 1 §5.3 P3/I3).

    Returns a dict for dict/parseable-JSON-string input, {} otherwise.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _apply_event(state: CaseState, event: dict) -> None:
    """Mutate state based on a single event. Called in sequence order.

    Supported event types:
        hypothesis_created -> add to state.hypotheses with initial_confidence
        hypothesis_version_created -> update state.hypotheses confidence
        evidence_ingested -> append evidence_id
        contradiction_detected -> append contradiction to state.contradictions
        contradiction_resolved -> remove from state.contradictions
        information_gap_created -> append gap_id to state.unresolved_gaps
        information_gap_resolved -> remove gap_id from state.unresolved_gaps
        hypothesis_status_changed -> append transition to state.state_changes
        Hari ICE improvements pass (audit: "claims, actions, approvals,
        executions, decisions, investigation threads and several other
        event types are ignored"):
        claim_extracted -> append claim to state.claims (count + ids)
        action_proposed / approved / executed / failed -> tracked in
            state.actions with status transitions
        decision_made -> appended to state.decisions
        investigation_thread_* -> tracked in state.threads
        induction_completed -> tracked in state.inductions
        Others ignored (no state impact for replay purposes) — but every
        applied event still increments event_count.
    """
    etype = event["event_type"]
    payload = event["payload"] or {}
    if isinstance(payload, str):  # pools without a jsonb codec return raw JSON
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            payload = {}

    if etype == "hypothesis_created":
        hid = payload.get("hypothesis_id")
        if hid:
            state.hypotheses[hid] = {
                "statement": payload.get("statement", ""),
                "confidence": float(payload.get("initial_confidence", 0.5)),
            }
    elif etype == "hypothesis_version_created":
        hid = payload.get("hypothesis_id")
        if hid:
            if hid not in state.hypotheses:
                state.hypotheses[hid] = {"statement": "", "confidence": 0.5}
            state.hypotheses[hid]["confidence"] = float(payload.get("new_confidence", 0.5))
    elif etype == "evidence_ingested":
        eid = payload.get("evidence_id")
        if eid:
            state.evidence_ids.append(eid)
    elif etype == "contradiction_detected":
        cid = payload.get("contradiction_id")
        if cid:
            state.contradictions.append({
                "contradiction_id": cid,
                "claim_a": payload.get("claim_a"),
                "claim_b": payload.get("claim_b"),
                "confidence": payload.get("confidence", 0.5),
            })
    elif etype == "contradiction_resolved":
        cid = payload.get("contradiction_id")
        state.contradictions = [
            c for c in state.contradictions if c["contradiction_id"] != cid
        ]
    elif etype == "information_gap_created":
        gid = payload.get("gap_id")
        if gid:
            state.unresolved_gaps.append(gid)
    elif etype == "information_gap_resolved":
        gid = payload.get("gap_id")
        state.unresolved_gaps = [g for g in state.unresolved_gaps if g != gid]
    elif etype == "hypothesis_status_changed":
        state.state_changes.append({
            "hypothesis_id": payload.get("hypothesis_id"),
            "old_status": payload.get("old_status"),
            "new_status": payload.get("new_status"),
        })
    # --- Hari ICE improvements pass: previously-ignored event types ------
    elif etype == "claim_extracted":
        for cid in (payload.get("claim_ids") or []):
            if cid and cid not in state.claims:
                state.claims.append(cid)
        if not payload.get("claim_ids"):
            cid = payload.get("claim_id")
            if cid and cid not in state.claims:
                state.claims.append(cid)
    elif etype == "action_proposed":
        aid = payload.get("action_id")
        if aid:
            state.actions[aid] = "proposed"
    elif etype == "action_approved":
        aid = payload.get("action_id")
        if aid:
            state.actions[aid] = "approved"
    elif etype == "action_executed":
        aid = payload.get("action_id")
        if aid:
            state.actions[aid] = "executed"
    elif etype == "action_failed":
        aid = payload.get("action_id")
        if aid:
            state.actions[aid] = "failed"
    elif etype == "decision_made":
        state.decisions.append({
            "decision_id": payload.get("decision_id"),
            "decision_type": payload.get("decision_type"),
            "confidence": payload.get("confidence"),
        })
    elif etype == "investigation_thread_created":
        tid = payload.get("thread_id")
        if tid and tid not in state.threads:
            state.threads.append(tid)
    elif etype == "investigation_thread_deactivated":
        tid = payload.get("thread_id")
        if tid in state.threads:
            state.threads.remove(tid)
    elif etype == "induction_completed":
        state.inductions.append({
            "hypotheses_created": payload.get("hypotheses_created") or [],
            "generation_path": payload.get("generation_path"),
        })

    state.event_count += 1


async def _compute_top_evidence_ids(
    case_id: str,
    evidence_ids: list[str],
    limit: int = 5,
) -> list[str]:
    """Rank the replayed evidence by quality_score and return the top ids.

    Read-only enrichment for CaseState.top_evidence_ids (audit gap #9).
    Returns [] when the pool is unavailable, the id list is empty, or the
    query fails — enrichment must never break a replay.
    """
    if not evidence_ids:
        return []
    pool = await get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT evidence_id FROM evidence
                WHERE evidence_id = ANY($1::text[])
                ORDER BY quality_score DESC, recorded_at ASC
                LIMIT $2
                """,
                evidence_ids, limit,
            )
        return [r["evidence_id"] for r in rows]
    except Exception as exc:
        logger.debug("top_evidence_ids enrichment failed for %s: %s", case_id, exc)
        return []


async def replay_case(
    case_id: str,
    up_to_event_seq: Optional[int] = None,
) -> CaseState:
    """Reconstruct case state from events.

    Postconditions:
        Returns a CaseState reflecting events applied in order
        (event_seq ASC, up to up_to_event_seq when given).
        Does not write to DB.
        top_evidence_ids is enriched from the replayed evidence's current
        quality_score (read-only; [] on any failure).
    Raises: DatabaseUnavailableError if the DB pool is None.
    """
    events = await _get_events(case_id, up_to_event_seq)
    state = CaseState.empty(case_id)
    for event in events:
        _apply_event(state, event)
    state.top_evidence_ids = await _compute_top_evidence_ids(
        case_id, state.evidence_ids
    )
    return state


async def replay_without_evidence(
    case_id: str,
    excluded_evidence_id: str,
) -> CaseState:
    """Replay the case as if a specific evidence item had never existed.

    Algorithm:
        Load all events.
        Filter out any event whose payload JSON contains the excluded
        evidence ID as a value in any string field.
        Replay the remaining events.

    Postconditions:
        Same as replay_case; top_evidence_ids is enriched from the
        *filtered* evidence set only, so the counterfactual stays hermetic.
    Raises: DatabaseUnavailableError if the DB pool is None.
        ValueError if ENABLE_REPLAY is false (spec §9 / §19.4 flag; the
        app layer surfaces it as 400 INVALID_REQUEST).
    """
    # ICE flag (spec §9 / §19.4). Read at CALL time — audit gap #39.
    # Counterfactual replay is the expensive path, so when the flag is
    # disabled this refuses loudly rather than silently degrading.
    if os.getenv("ENABLE_REPLAY", "true").lower() != "true":
        raise ValueError(
            "Counterfactual replay is disabled (ENABLE_REPLAY=false)"
        )

    events = await _get_events(case_id)
    filtered = []
    for event in events:
        payload_str = json.dumps(event["payload"], default=str)
        if excluded_evidence_id in payload_str:
            continue
        filtered.append(event)
    state = CaseState.empty(case_id)
    for event in filtered:
        _apply_event(state, event)

    # ------------------------------------------------------------------
    # Hari ICE improvements pass — THE counterfactual fix. Previously this
    # function returned the *recorded* confidences of the surviving
    # hypothesis_version events, so removing a piece of evidence often
    # changed nothing (the audit: "the subsequent hypothesis-version events
    # may still preserve the original conclusion even after removing the
    # evidence event ... That is not a defensible counterfactual causal
    # experiment"). Now the hypothesis confidences are REBUILT from the
    # remaining evidence links using the same scoring math as
    # hypothesis.recompute_hypothesis_confidence (support − contra sigmoid
    # over lineage-capped evidence weights), with the excluded evidence
    # (and anything it derived) removed from the link set. The projection
    # then reflects what the inference WOULD have concluded without that
    # evidence.
    # ------------------------------------------------------------------
    rebuilt = await _recompute_counterfactual_confidences(
        case_id, excluded_evidence_id
    )
    for hid, new_conf in rebuilt.items():
        if hid in state.hypotheses:
            state.hypotheses[hid]["confidence"] = new_conf
            state.hypotheses[hid]["counterfactual_recomputed"] = True

    state.top_evidence_ids = await _compute_top_evidence_ids(
        case_id, state.evidence_ids
    )
    return state


async def _recompute_counterfactual_confidences(
    case_id: str,
    excluded_evidence_id: str,
) -> dict[str, float]:
    """Rebuild every hypothesis confidence minus one evidence item.

    Pure read path: loads the same link data as
    hypothesis.recompute_hypothesis_confidence, filters out the excluded
    evidence (and evidence whose lineage derives from it), and applies the
    identical sigmoid formula. Returns {hypothesis_id: confidence}.
    Failures degrade to {} (the caller keeps the filtered-event projection)
    — a broken counterfactual must never silently look authoritative.
    """
    try:
        from case_engine.hypothesis import (
            _independent_lineage_groups,
            _evidence_weight,
        )

        pool = await get_pool()
        if pool is None:
            return {}
        async with pool.acquire() as conn:
            claim_rows = await conn.fetch(
                """
                SELECT c.claim_id, c.confidence,
                       c.supporting_evidence_ids, c.contradicting_evidence_ids,
                       e.to_node AS hypothesis_id, e.relation
                FROM claims c
                JOIN evidence_graph_edges e
                  ON e.from_node = c.claim_id AND e.from_type = 'claim'
                JOIN hypotheses h ON h.hypothesis_id = e.to_node
                WHERE h.case_id = $1
                  AND e.relation IN ('SUPPORTS', 'CONTRADICTS')
                """,
                case_id,
            )
            direct_rows = await conn.fetch(
                """
                SELECT e.from_node AS evidence_id, e.relation, e.weight,
                       e.to_node AS hypothesis_id
                FROM evidence_graph_edges e
                JOIN hypotheses h ON h.hypothesis_id = e.to_node
                WHERE h.case_id = $1
                  AND e.from_type = 'evidence'
                  AND e.relation IN ('SUPPORTS', 'CONTRADICTS')
                """,
                case_id,
            )
            # every evidence item that descends from the excluded one
            # (supersession chain) is excluded too — removing the original
            # must remove its derivatives.
            derived = await conn.fetch(
                """
                WITH RECURSIVE chain AS (
                    SELECT evidence_id FROM evidence WHERE evidence_id = $1
                    UNION ALL
                    SELECT ev.evidence_id FROM evidence ev
                    JOIN chain c ON ev.supersedes = c.evidence_id
                )
                SELECT evidence_id FROM chain
                """,
                excluded_evidence_id,
            )
        excluded_set = {excluded_evidence_id} | {
            r["evidence_id"] for r in derived
        }

        support: dict[str, float] = {}
        contra: dict[str, float] = {}

        def _add(d: dict, hid: str, value: float) -> None:
            d[hid] = d.get(hid, 0.0) + value

        for row in claim_rows:
            hid = row["hypothesis_id"]
            supp = [e for e in (row["supporting_evidence_ids"] or [])
                    if e not in excluded_set]
            contra_ev = [e for e in (row["contradicting_evidence_ids"] or [])
                         if e not in excluded_set]
            rel = row["relation"]
            # polarity inversion for CONTRADICTS claims (Correction 6)
            if rel == "SUPPORTS":
                for group in await _independent_lineage_groups(supp):
                    strength = sum([await _evidence_weight(e) for e in group])
                    _add(support, hid, row["confidence"] * min(1.0, strength))
                for group in await _independent_lineage_groups(contra_ev):
                    strength = sum([await _evidence_weight(e) for e in group])
                    _add(contra, hid, row["confidence"] * min(1.0, strength))
            else:
                for group in await _independent_lineage_groups(supp):
                    strength = sum([await _evidence_weight(e) for e in group])
                    _add(contra, hid, row["confidence"] * min(1.0, strength))
                for group in await _independent_lineage_groups(contra_ev):
                    strength = sum([await _evidence_weight(e) for e in group])
                    _add(support, hid, row["confidence"] * min(1.0, strength))

        for row in direct_rows:
            if row["evidence_id"] in excluded_set:
                continue
            hid = row["hypothesis_id"]
            w = float(row["weight"] if row["weight"] is not None else 1.0)
            ev_w = await _evidence_weight(row["evidence_id"])
            if row["relation"] == "SUPPORTS":
                _add(support, hid, w * ev_w)
            else:
                _add(contra, hid, w * ev_w)

        # EVERY hypothesis in the case gets a rebuilt value — including
        # ones whose support was entirely removed (they fall to the
        # no-evidence sigmoid midpoint 0.5, NOT to their recorded value).
        # The original version iterated only over hypotheses with
        # remaining support/contra entries, so removing ALL of a
        # hypothesis's evidence left its recorded confidence untouched —
        # exactly the non-counterfactual behavior the audit rejected.
        async with pool.acquire() as conn:
            hyp_rows = await conn.fetch(
                "SELECT hypothesis_id FROM hypotheses WHERE case_id = $1",
                case_id,
            )
        out: dict[str, float] = {}
        for r in hyp_rows:
            hid = r["hypothesis_id"]
            raw = support.get(hid, 0.0) - contra.get(hid, 0.0)
            out[hid] = 1.0 / (1.0 + math.exp(-raw))
        return out
    except Exception as exc:
        logger.debug("counterfactual recompute failed: %s", exc)
        return {}


async def compute_evidence_causal_influence(
    case_id: str,
    evidence_id: str,
) -> dict:
    """Measure how much this evidence item affected the case outcome.

    Postconditions:
        Returns a dict with keys:
            evidence_id, original_confidence, counterfactual_confidence,
            causal_influence, interpretation.
        causal_influence = |original_confidence - counterfactual_confidence|
        (both are the leading-hypothesis confidences of the full replay and
        the counterfactual replay respectively).
        interpretation is one of:
            "decision_critical" (influence > 0.2)
            "moderate" (0.05 < influence <= 0.2)
            "negligible" (influence <= 0.05)
    Raises: DatabaseUnavailableError (via the replays).
    """
    original_state = await replay_case(case_id)
    counterfactual_state = await replay_without_evidence(case_id, evidence_id)

    original_conf = original_state.leading_hypothesis_confidence
    counterfactual_conf = counterfactual_state.leading_hypothesis_confidence

    influence = abs(original_conf - counterfactual_conf)
    if influence > 0.2:
        interpretation = "decision_critical"
    elif influence > 0.05:
        interpretation = "moderate"
    else:
        interpretation = "negligible"

    return {
        "evidence_id": evidence_id,
        "original_confidence": original_conf,
        "counterfactual_confidence": counterfactual_conf,
        "causal_influence": influence,
        "interpretation": interpretation,
    }


async def compute_decision_attribution(decision_id: str) -> dict:
    """Return the full attribution for a decision.

    Postconditions:
        Returns a dict with keys:
            decision_id, case_id, decision_type, rationale, confidence,
            top_factors, alternatives, recommended_action_id.
    Raises:
        DatabaseUnavailableError: if the DB pool is None.
    Returns {} if the decision is not found.
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot load decision: no DB pool")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM decisions WHERE decision_id = $1", decision_id
        )
    if row is None:
        return {}

    # P3/I3 (merge spec Part 1 §5.3, Manual Patch 1 loc. 1): jsonb columns
    # can come back as raw JSON strings; parse defensively.
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

    return {
        "decision_id": row["decision_id"],
        "case_id": row["case_id"],
        "decision_type": row["decision_type"],
        "rationale": row["rationale"],
        "confidence": row["confidence"],
        "top_factors": top_f or {},
        "alternatives": alts or [],
        "recommended_action_id": row["recommended_action_id"],
    }