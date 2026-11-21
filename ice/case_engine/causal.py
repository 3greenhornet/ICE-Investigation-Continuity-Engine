"""case_engine/causal.py — causal explanation layer + ACH matrix.

Owner:      Hari ICE "addition and improvements" pass. Two audit gaps:
            (1) "The evidence graph is not yet a causal model ... an edge
            saying EVIDENCE SUPPORTS HYPOTHESIS does not mean the evidence
            establishes a causal relationship." This module adds a SEPARATE
            causal layer (migration 018 Part C): nodes are variables/events/
            components/conditions, edges are PROPOSED causal relationships
            each carrying provenance, confidence, assumptions and temporal
            scope — reviewed by a human before they mean anything
            (proposed → accepted/rejected lifecycle, like proposed_edges).
            (2) Intelligence-analysis ACH: "Don't evaluate each explanation
            independently. Evaluate the evidence against all explanations
            in a common matrix." build_ach_matrix() produces that matrix
            plus its diagnostics (least-contradicted hypothesis,
            undiscriminating evidence).

Honesty note: this is NOT a probabilistic causal model. There is no
do-calculus, no Bayesian posterior, no pgmpy. Edges are reviewed claims
about causal structure; `confidence` is a heuristic in [0,1], never a
calibrated probability (README's standing caveat applies). The
intervention query answers from ACCEPTED edges only, and says "unknown"
rather than inventing structure.
Deps:       db.connection, case_engine (_emit_event), exceptions, claim,
            hypothesis, timeline (lazily).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from db.connection import get_pool
from case_engine import _emit_event
from case_engine.exceptions import DatabaseUnavailableError

logger = logging.getLogger(__name__)

NODE_TYPES = ("event", "component", "variable", "condition", "hypothesis", "claim")
RELATIONS = ("CAUSES", "CONTRIBUTES_TO", "PRECEDES", "EXCLUDES", "CORRELATES_WITH")


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2
    }


def _row_to_edge(row) -> dict:
    return {
        "causal_edge_id": row["causal_edge_id"],
        "case_id": row["case_id"],
        "cause_node_type": row["cause_node_type"],
        "cause_node_id": row["cause_node_id"],
        "cause_label": row["cause_label"],
        "effect_node_type": row["effect_node_type"],
        "effect_node_id": row["effect_node_id"],
        "effect_label": row["effect_label"],
        "relation": row["relation"],
        "confidence": float(row["confidence"]),
        "assumptions": list(row["assumptions"] or []),
        "valid_from": row["valid_from"],
        "valid_until": row["valid_until"],
        "provenance": row["provenance"],
        "proposed_by": row["proposed_by"],
        "status": row["status"],
        "review_note": row["review_note"],
        "created_at": row["created_at"],
    }


async def propose_causal_edge(
    case_id: str,
    cause_node_type: str,
    cause_node_id: str,
    cause_label: str,
    effect_node_type: str,
    effect_node_id: str,
    effect_label: str,
    relation: str = "CONTRIBUTES_TO",
    confidence: float = 0.5,
    assumptions: Optional[list[str]] = None,
    valid_from=None,
    valid_until=None,
    provenance: Optional[dict] = None,
    proposed_by: str = "ice",
) -> dict:
    """Record one PROPOSED causal relationship (human review pending).

    Postconditions:
        causal_edges row with status='proposed' (or the existing row
        returned unchanged on the unique-tuple collision).
        causal_edge_proposed event emitted.
    """
    if cause_node_type not in NODE_TYPES or effect_node_type not in NODE_TYPES:
        raise ValueError("invalid node type")
    if relation not in RELATIONS:
        raise ValueError(f"relation must be one of {RELATIONS}")
    if not (0.0 <= confidence <= 1.0):
        raise ValueError("confidence must be in [0, 1]")

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot propose causal edge: no DB pool")

    edge_id = f"ce_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO causal_edges (
                        causal_edge_id, case_id, cause_node_type, cause_node_id,
                        cause_label, effect_node_type, effect_node_id,
                        effect_label, relation, confidence, assumptions,
                        valid_from, valid_until, provenance, proposed_by,
                        status, created_at
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::text[],
                        $12,$13,$14::jsonb,$15,'proposed',$16
                    )
                    """,
                    edge_id, case_id, cause_node_type, cause_node_id,
                    cause_label, effect_node_type, effect_node_id,
                    effect_label, relation, confidence, assumptions or [],
                    valid_from, valid_until,
                    json.dumps(provenance or {}, default=str),
                    proposed_by, now,
                )
                await _emit_event(
                    case_id, "causal_edge_proposed",
                    {
                        "causal_edge_id": edge_id,
                        "relation": relation,
                        "cause": f"{cause_node_type}:{cause_label or cause_node_id}",
                        "effect": f"{effect_node_type}:{effect_label or effect_node_id}",
                        "confidence": confidence,
                        "proposed_by": proposed_by,
                    },
                    conn=conn,
                )
    except Exception as exc:
        if "unique" in str(exc).lower():
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM causal_edges
                    WHERE case_id = $1 AND cause_node_type = $2
                      AND cause_node_id = $3 AND effect_node_type = $4
                      AND effect_node_id = $5 AND relation = $6
                    """,
                    case_id, cause_node_type, cause_node_id,
                    effect_node_type, effect_node_id, relation,
                )
            if row is not None:
                return _row_to_edge(row)
        raise

    return await get_causal_edge(edge_id)


async def get_causal_edge(causal_edge_id: str) -> dict:
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM causal_edges WHERE causal_edge_id = $1",
            causal_edge_id,
        )
    if row is None:
        raise ValueError(f"Causal edge not found: {causal_edge_id}")
    return _row_to_edge(row)


async def list_causal_edges(
    case_id: str, status: Optional[str] = None
) -> list[dict]:
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    if status:
        sql = "SELECT * FROM causal_edges WHERE case_id = $1 AND status = $2 ORDER BY created_at"
        params = (case_id, status)
    else:
        sql = "SELECT * FROM causal_edges WHERE case_id = $1 ORDER BY created_at"
        params = (case_id,)
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [_row_to_edge(r) for r in rows]


async def review_causal_edge(
    causal_edge_id: str, decision: str, note: str = ""
) -> dict:
    """Accept or reject a proposed causal edge (human review boundary).

    Only ACCEPTED edges participate in intervention queries — an unreviewed
    proposal is an opinion, not a model.
    """
    if decision not in ("accepted", "rejected"):
        raise ValueError("decision must be 'accepted' or 'rejected'")
    current = await get_causal_edge(causal_edge_id)
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE causal_edges
                SET status = $1, review_note = $2, reviewed_at = $3
                WHERE causal_edge_id = $4
                """,
                decision, note, now, causal_edge_id,
            )
            await _emit_event(
                current["case_id"], "causal_edge_reviewed",
                {
                    "causal_edge_id": causal_edge_id,
                    "decision": decision,
                    "note": note,
                },
                conn=conn,
            )
    return await get_causal_edge(causal_edge_id)


# ---------------------------------------------------------------------------
# Deterministic temporal-adjacency proposals
# ---------------------------------------------------------------------------


async def propose_edges_from_timeline(case_id: str) -> list[dict]:
    """Propose PRECEDES edges from the reconstructed timeline.

    For each adjacent pair of dated timeline entries a PRECEDES edge is
    proposed (confidence scaled by temporal proximity — the closer the
    events, the less arbitrary the ordering). Deliberately conservative:
    this proposes PRECEDES, never CAUSES — "a temporal order alone never
    justifies a CAUSES edge".
    """
    from case_engine.timeline import build_case_timeline

    timeline = await build_case_timeline(case_id)
    dated = [e for e in timeline if e.ts is not None]
    proposals: list[dict] = []
    for prev, nxt in zip(dated, dated[1:]):
        if prev.source_id == nxt.source_id:
            continue
        gap = (nxt.ts - prev.ts).total_seconds()
        if gap <= 0:
            continue
        # proximity confidence: 0.9 at <1min, decaying to 0.3 at 1 day
        confidence = max(0.3, min(0.9, 0.9 - 0.6 * (gap / 86400.0)))
        proposals.append(
            await propose_causal_edge(
                case_id=case_id,
                cause_node_type="event",
                cause_node_id=prev.source_id or prev.label,
                cause_label=prev.label,
                effect_node_type="event",
                effect_node_id=nxt.source_id or nxt.label,
                effect_label=nxt.label,
                relation="PRECEDES",
                confidence=round(confidence, 3),
                assumptions=[
                    "Timestamps are comparable across sources (clock skew unverified)",
                ],
                provenance={"origin": "timeline_adjacency", "gap_seconds": gap},
                proposed_by="ice",
            )
        )
    return proposals


async def predict_intervention(case_id: str, node_id: str) -> dict:
    """What should change if we intervene on one node (accepted edges only).

    Answers from ACCEPTED CAUSES/CONTRIBUTES_TO edges downstream of
    node_id. Returns {downstream: [...], unknown_paths_note} — when no
    accepted edges exist the honest answer is "unknown", never a guess.
    """
    accepted = [
        e for e in await list_causal_edges(case_id, status="accepted")
        if e["relation"] in ("CAUSES", "CONTRIBUTES_TO")
    ]
    downstream: list[dict] = []
    frontier = [node_id]
    seen = {node_id}
    while frontier:
        current = frontier.pop()
        for e in accepted:
            if e["cause_node_id"] == current and e["effect_node_id"] not in seen:
                seen.add(e["effect_node_id"])
                downstream.append(
                    {
                        "node_id": e["effect_node_id"],
                        "label": e["effect_label"],
                        "relation": e["relation"],
                        "confidence": e["confidence"],
                    }
                )
                frontier.append(e["effect_node_id"])
    return {
        "intervention_node": node_id,
        "expected_changes": downstream,
        "basis": f"{len(accepted)} accepted causal edges",
        "unknown_paths_note": (
            "Only human-accepted causal edges are used. Absence of an edge "
            "means the model does not know — not that no effect exists."
        ),
    }


# ---------------------------------------------------------------------------
# ACH — Analysis of Competing Hypotheses
# ---------------------------------------------------------------------------


async def build_ach_matrix(case_id: str) -> dict:
    """Evidence × hypotheses matrix with support/contradict ratings.

    The CIA Tradecraft ACH inversion: instead of scoring each hypothesis on
    its own (confirmation-friendly), every claim is rated against EVERY
    hypothesis, and the diagnostics report:
      * least_contradicted — the hypothesis with the fewest contradictions
        (ACH's actual decision rule: consistency, not supporting-evidence
        count)
      * most_discriminating — claims that rate differently across
        hypotheses (worth gathering MORE of)
      * undiscriminating — claims that rate the same everywhere (however
        agreeable, they cannot separate explanations)
    Ratings are deterministic token-overlap + polarity heuristics against
    hypothesis statements and their linked claims (SUPPORTS/CONTRADICTS
    edges), NOT LLM judgments — the matrix is meant to be inspectable.
    """
    from case_engine.claim import get_claims_for_case
    from case_engine.hypothesis import (
        list_hypotheses_for_case,
        get_current_version,
    )

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot build ACH matrix: no DB pool")

    hypotheses = [
        h for h in await list_hypotheses_for_case(case_id)
        if h.status not in ("FALSIFIED",)
    ]
    claims = [c for c in await get_claims_for_case(case_id) if c.status == "ACTIVE"]

    # claim -> {hypothesis_id: relation} from the evidence graph
    edge_map: dict[str, dict[str, str]] = {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT e.from_node AS claim_id, e.to_node AS hypothesis_id,
                   e.relation
            FROM evidence_graph_edges e
            WHERE e.from_type = 'claim' AND e.to_type = 'hypothesis'
              AND e.relation IN ('SUPPORTS', 'CONTRADICTS')
              AND e.to_node = ANY($1::text[])
            """,
            [h.hypothesis_id for h in hypotheses],
        )
    for r in rows:
        edge_map.setdefault(r["claim_id"], {})[r["hypothesis_id"]] = r["relation"]

    matrix: dict[str, dict[str, str]] = {}
    hypothesis_tokens = {h.hypothesis_id: _tokens(h.statement) for h in hypotheses}
    claim_rows: list[dict] = []

    for c in claims:
        row: dict[str, str] = {}
        ctoks = _tokens(c.statement)
        negated = any(
            neg in c.statement.lower()
            for neg in ("not", "no ", "never", "unchanged", "ruled out", "without")
        )
        for h in hypotheses:
            explicit = edge_map.get(c.claim_id, {}).get(h.hypothesis_id)
            if explicit == "SUPPORTS":
                row[h.hypothesis_id] = "S"
            elif explicit == "CONTRADICTS":
                row[h.hypothesis_id] = "C"
            else:
                overlap = ctoks & hypothesis_tokens[h.hypothesis_id]
                if overlap and not negated:
                    row[h.hypothesis_id] = "s"   # weak support
                elif overlap and negated:
                    row[h.hypothesis_id] = "c"   # weak contradiction
                else:
                    row[h.hypothesis_id] = "-"   # neutral
        matrix[c.claim_id] = row
        claim_rows.append({"claim_id": c.claim_id, "statement": c.statement})

    # --- diagnostics -------------------------------------------------------
    contradiction_counts = {
        h.hypothesis_id: sum(
            1 for row in matrix.values()
            if row.get(h.hypothesis_id) in ("C", "c")
        )
        for h in hypotheses
    }
    support_counts = {
        h.hypothesis_id: sum(
            1 for row in matrix.values()
            if row.get(h.hypothesis_id) in ("S", "s")
        )
        for h in hypotheses
    }
    least_contradicted = None
    if contradiction_counts:
        best = min(contradiction_counts.values())
        least_contradicted = [
            hid for hid, n in contradiction_counts.items() if n == best
        ]

    discriminating: list[str] = []
    undiscriminating: list[str] = []
    for claim_id, row in matrix.items():
        values = {v for v in row.values()}
        strong_values = {v for v in values if v in ("S", "C")}
        if len(strong_values) > 1 or ("S" in values and "C" in values):
            discriminating.append(claim_id)
        elif len({v for v in values if v != "-"}) <= 1:
            undiscriminating.append(claim_id)

    confidences = {}
    for h in hypotheses:
        try:
            v = await get_current_version(h.hypothesis_id)
            confidences[h.hypothesis_id] = v.confidence
        except Exception:
            confidences[h.hypothesis_id] = None

    return {
        "hypotheses": [
            {
                "hypothesis_id": h.hypothesis_id,
                "statement": h.statement,
                "contradiction_count": contradiction_counts[h.hypothesis_id],
                "support_count": support_counts[h.hypothesis_id],
                "confidence": confidences[h.hypothesis_id],
            }
            for h in hypotheses
        ],
        "claims": claim_rows,
        "matrix": matrix,
        "legend": {
            "S": "explicit SUPPORTS edge (evidence graph)",
            "C": "explicit CONTRADICTS edge (evidence graph)",
            "s": "token-overlap heuristic support",
            "c": "token-overlap heuristic contradiction",
            "-": "neutral / unrelated",
        },
        "diagnostics": {
            "least_contradicted": least_contradicted,
            "most_discriminating_claims": discriminating,
            "undiscriminating_claims": undiscriminating,
            "rule": (
                "ACH decision rule: prefer the hypothesis with the FEWEST "
                "contradictions; seek more of the discriminating claims, "
                "discount the undiscriminating ones."
            ),
        },
    }
