"""case_engine/hypothesis.py — hypothesis plane of the ICE case engine.

Owner: ICE revamp Part 2 §5 (implementation task ice-2-b).
Purpose: versioned hypotheses with traceable belief revision. Every confidence
change is a new version. No in-place mutation.

Downstream deps:
    - case_engine.contradiction / information_gap / falsification (Part 3)
    - case_engine.decision + next_best_action (Part 3)
    - app routes (Part 4) and scripts/verify_part2.py
    - engine TurnPipeline case mode (Part 4 §16)

Upstream deps (owned by ice-2-a / ice-2-b):
    - db.connection.get_pool
    - case_engine._emit_event
    - case_engine.exceptions (DatabaseUnavailableError, HypothesisNotFoundError)
    - case_engine.schemas (Hypothesis, HypothesisVersion, HypothesisStatus)
    - case_engine.evidence.get_evidence (this repo, ice-2-b)
    - db/migrations/012_case_model.sql (hypotheses / hypothesis_versions tables)
"""

from __future__ import annotations

import logging
import math
import os
import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal, Optional

from db.connection import get_pool
from case_engine import _emit_event
from case_engine.exceptions import (
    DatabaseUnavailableError,
    HypothesisNotFoundError,
)
from case_engine.schemas import Hypothesis, HypothesisVersion, HypothesisStatus
from case_engine.evidence import get_evidence

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a hard runtime dep
    import asyncpg

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# §5.3 Statement normalization and row mapping
# ---------------------------------------------------------------------------

def _normalize_statement(statement: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace (sync)."""
    s = statement.strip().lower()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _row_to_hypothesis(row: asyncpg.Record) -> Hypothesis:
    """Map an asyncpg row to Hypothesis. Explicit column mapping.

    Part 2 §1.2: ``origin`` / ``origin_detail`` are read with row.keys()
    guards so a database that has not yet applied migration 015 maps rows
    with the "user" / None defaults instead of crashing.
    """
    origin = row["origin"] if "origin" in row.keys() else "user"
    origin_detail = (
        row["origin_detail"] if "origin_detail" in row.keys() else None
    )
    return Hypothesis(
        hypothesis_id=row["hypothesis_id"],
        case_id=row["case_id"],
        statement=row["statement"],
        normalized_statement=row["normalized_statement"],
        status=row["status"],
        current_version_id=row["current_version_id"],
        created_at=row["created_at"],
        origin=origin,
        origin_detail=origin_detail,
    )


def _row_to_version(row: asyncpg.Record) -> HypothesisVersion:
    """Map an asyncpg row to HypothesisVersion. Explicit column mapping."""
    return HypothesisVersion(
        version_id=row["version_id"],
        hypothesis_id=row["hypothesis_id"],
        version_number=row["version_number"],
        confidence=row["confidence"],
        prior_confidence=row["prior_confidence"],
        supporting_claim_ids=list(row["supporting_claim_ids"] or []),
        contradicting_claim_ids=list(row["contradicting_claim_ids"] or []),
        supporting_evidence_added=list(row["supporting_evidence_added"] or []),
        supporting_evidence_removed=list(row["supporting_evidence_removed"] or []),
        contradicting_evidence_added=list(row["contradicting_evidence_added"] or []),
        reason=row["reason"],
        cause_event_id=row["cause_event_id"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# §5.4 Creation
# ---------------------------------------------------------------------------

async def create_hypothesis(
    case_id: str,
    statement: str,
    initial_confidence: float = 0.5,
    supporting_claim_ids: Optional[list[str]] = None,
    reason: str = "Initial hypothesis",
    cause_event_id: Optional[str] = None,
    origin: str = "user",
    origin_detail: Optional[str] = None,
) -> Hypothesis:
    """Create a hypothesis with version 1.

    Preconditions:
        case_id exists.
        statement is non-empty.
        0.0 <= initial_confidence <= 1.0.
        origin is one of "user", "external_llm", "ice_generated",
        "source_material" (Part 2 §2.3).
    Postconditions:
        hypothesis row exists with normalized_statement set, origin and
        origin_detail persisted (Part 2 §2.3).
        current_version_id points to a version 1 row (prior_confidence NULL).
        hypothesis_created event emitted (payload includes origin).
    Raises:
        ValueError on bad input (including an invalid origin), or when
        normalized_statement collides within the case (asyncpg
        UniqueViolationError proxied as ValueError).
        DatabaseUnavailableError.
    Side effects:
        INSERT INTO hypotheses (1).
        INSERT INTO hypothesis_versions (1).
        _emit_event("hypothesis_created", {...}).
    """
    if not statement or not statement.strip():
        raise ValueError("statement must be non-empty")
    if not (0.0 <= initial_confidence <= 1.0):
        raise ValueError("initial_confidence must be in [0, 1]")
    if origin not in ("user", "external_llm", "ice_generated", "source_material"):
        raise ValueError(f"Invalid origin: {origin}")

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot create hypothesis: no DB pool")

    hypothesis_id = f"h_{uuid.uuid4().hex[:12]}"
    version_id = f"hv_{uuid.uuid4().hex[:12]}"
    cause_id = cause_event_id or f"evt_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    normalized = _normalize_statement(statement)

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO hypotheses (
                        hypothesis_id, case_id, statement, normalized_statement,
                        status, current_version_id, created_at,
                        origin, origin_detail
                    ) VALUES ($1, $2, $3, $4, 'PROPOSED', $5, $6, $7, $8)
                    """,
                    hypothesis_id,
                    case_id,
                    statement.strip(),
                    normalized,
                    version_id,
                    now,
                    origin,
                    origin_detail,
                )
                await conn.execute(
                    """
                    INSERT INTO hypothesis_versions (
                        version_id, hypothesis_id, version_number,
                        confidence, prior_confidence,
                        supporting_claim_ids, contradicting_claim_ids,
                        supporting_evidence_added, supporting_evidence_removed,
                        contradicting_evidence_added,
                        reason, cause_event_id, created_at
                    ) VALUES (
                        $1, $2, 1, $3, NULL,
                        $4::text[], '{}',
                        '{}', '{}', '{}',
                        $5, $6, $7
                    )
                    """,
                    version_id,
                    hypothesis_id,
                    initial_confidence,
                    supporting_claim_ids or [],
                    reason,
                    cause_id,
                    now,
                )
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise ValueError(
                f"Hypothesis with normalized_statement already exists in case {case_id}"
            ) from exc
        raise

    await _emit_event(
        case_id,
        "hypothesis_created",
        {
            "hypothesis_id": hypothesis_id,
            "statement": statement.strip(),
            "initial_confidence": initial_confidence,
            "origin": origin,
        },
    )

    return await get_hypothesis(hypothesis_id)


# ---------------------------------------------------------------------------
# §5.5 Reads
# ---------------------------------------------------------------------------

async def get_hypothesis(hypothesis_id: str) -> Hypothesis:
    """Load a hypothesis by ID. Raises HypothesisNotFoundError."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot load hypothesis: no DB pool")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM hypotheses WHERE hypothesis_id = $1", hypothesis_id
        )
    if row is None:
        raise HypothesisNotFoundError(f"Hypothesis not found: {hypothesis_id}")
    return _row_to_hypothesis(row)


async def get_current_version(hypothesis_id: str) -> HypothesisVersion:
    """Load the current version of a hypothesis.

    Raises HypothesisNotFoundError if either the hypothesis or version is
    missing.
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot load version: no DB pool")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT hv.* FROM hypothesis_versions hv
            JOIN hypotheses h ON h.current_version_id = hv.version_id
            WHERE h.hypothesis_id = $1
            """,
            hypothesis_id,
        )
    if row is None:
        raise HypothesisNotFoundError(f"No current version for {hypothesis_id}")
    return _row_to_version(row)


async def get_version_history(hypothesis_id: str) -> list[HypothesisVersion]:
    """Return all versions ordered by version_number DESC."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot load versions: no DB pool")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM hypothesis_versions
            WHERE hypothesis_id = $1
            ORDER BY version_number DESC
            """,
            hypothesis_id,
        )
    return [_row_to_version(row) for row in rows]


# ---------------------------------------------------------------------------
# §5.6 Versioned update
# ---------------------------------------------------------------------------

async def update_hypothesis(
    hypothesis_id: str,
    new_confidence: float,
    reason: str,
    cause_event_id: str,
    added_supporting_evidence: Optional[list[str]] = None,
    removed_supporting_evidence: Optional[list[str]] = None,
    added_contradicting_evidence: Optional[list[str]] = None,
) -> HypothesisVersion:
    """Create a new version. Never mutates the prior version.

    Preconditions:
        hypothesis_id exists.
        0.0 <= new_confidence <= 1.0.
        reason is non-empty.
        cause_event_id is provided.
    Postconditions:
        hypothesis_versions has a new row with version_number = current + 1.
        hypotheses.current_version_id points to the new version.
        hypothesis_version_created event emitted.
    Raises:
        HypothesisNotFoundError.
        ValueError on invalid confidence or empty reason.
        DatabaseUnavailableError.
    Side effects:
        INSERT INTO hypothesis_versions (1).
        UPDATE hypotheses (1).
        _emit_event("hypothesis_version_created", {...}).
    """
    if not (0.0 <= new_confidence <= 1.0):
        raise ValueError("new_confidence must be in [0, 1]")
    if not reason or not reason.strip():
        raise ValueError("reason must be non-empty")

    current = await get_current_version(hypothesis_id)
    hypothesis = await get_hypothesis(hypothesis_id)

    new_version_number = current.version_number + 1
    new_version_id = f"hv_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot update hypothesis: no DB pool")

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO hypothesis_versions (
                    version_id, hypothesis_id, version_number,
                    confidence, prior_confidence,
                    supporting_claim_ids, contradicting_claim_ids,
                    supporting_evidence_added, supporting_evidence_removed,
                    contradicting_evidence_added,
                    reason, cause_event_id, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5,
                    $6::text[], $7::text[],
                    $8::text[], $9::text[], $10::text[],
                    $11, $12, $13
                )
                """,
                new_version_id,
                hypothesis_id,
                new_version_number,
                new_confidence,
                current.confidence,
                current.supporting_claim_ids,
                current.contradicting_claim_ids,
                added_supporting_evidence or [],
                removed_supporting_evidence or [],
                added_contradicting_evidence or [],
                reason.strip(),
                cause_event_id,
                now,
            )
            await conn.execute(
                "UPDATE hypotheses SET current_version_id = $1 WHERE hypothesis_id = $2",
                new_version_id,
                hypothesis_id,
            )

    await _emit_event(
        hypothesis.case_id,
        "hypothesis_version_created",
        {
            "hypothesis_id": hypothesis_id,
            "version_id": new_version_id,
            "version_number": new_version_number,
            "prior_confidence": current.confidence,
            "new_confidence": new_confidence,
            "reason": reason.strip(),
            "cause_event_id": cause_event_id,
        },
    )

    return await get_current_version(hypothesis_id)


# ---------------------------------------------------------------------------
# §5.7 Independence bookkeeping
# ---------------------------------------------------------------------------

async def _independent_lineage_groups(
    evidence_ids: list[str],
) -> list[list[str]]:
    """Group evidence by lineage cluster.

    Preconditions: none.
    Postconditions:
        Returns a list of groups. Each group shares a source_lineage_id.
        Evidence with no lineage falls into its own group.
        If ENABLE_SOURCE_INDEPENDENCE is false (spec §9 / §19.4), every
        evidence item is treated as its own group — duplicate evidence from
        the same source then counts at full weight instead of being capped
        at 1.0 per lineage (read at CALL time, audit gap #39).
    Raises: nothing.
    Side effects: none.
    """
    if not evidence_ids:
        return []
    if os.getenv("ENABLE_SOURCE_INDEPENDENCE", "true").lower() != "true":
        return [[eid] for eid in evidence_ids]
    pool = await get_pool()
    if pool is None:
        return [[eid] for eid in evidence_ids]

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT evidence_id, source_lineage_id, duplicate_cluster_id
            FROM evidence
            WHERE evidence_id = ANY($1::text[])
            """,
            evidence_ids,
        )
    # Hari ICE improvements pass (second-pass audit: "correlated sources
    # without shared recorded ancestry can still be overcounted"): near-
    # duplicates detected at ingestion time (duplicate_cluster_id) are
    # grouped together EVEN when their lineages differ — ten articles
    # repeating one source are not ten independent confirmations.
    groups: dict[str, list[str]] = {}
    cluster_to_lineage: dict[str, str] = {}
    for row in rows:
        lineage = row["source_lineage_id"] or row["evidence_id"]
        cluster = row["duplicate_cluster_id"]
        if cluster:
            # all members of a duplicate cluster collapse onto the first
            # lineage seen for that cluster
            lineage = cluster_to_lineage.setdefault(cluster, lineage)
        groups.setdefault(lineage, []).append(row["evidence_id"])
    return list(groups.values())


async def _evidence_weight(evidence_id: str) -> float:
    """Return the weight for an evidence item in support calculations.

    - ICE's own MODEL_INFERENCE / MODEL_SUMMARY: 0.0
      (self-confirmation guard: the model can never confirm itself)
    - External LLM analysis (source_llm is set): 0.4
      (a second opinion, valuable but not ground truth)
    - Human assertions, measurements, tool results: quality_score (0.0-1.0)

    The distinction for MODEL_INFERENCE is made by evidence.source_llm,
    which is populated only by ingest_external_analysis(). If migration
    015 isn't applied, source_llm is None for all evidence, and this
    behaves as before — zero weight for all MODEL_INFERENCE.

    HONEST NOTE (doc Part 2 §12 shortcut #1): the 0.4 external-LLM weight
    is a made-up round number between 0.0 (ICE self-inference, banned for
    self-confirmation reasons) and 1.0 (perfect human observation). It is
    a placeholder, not a calibrated value — the principled version would
    calibrate against how often external LLM analyses turn out to be
    right, per LLM and model version. The (LLM, prompt-hash) dedup in
    ingest_external_analysis mitigates the obvious gaming case but does
    not calibrate the weight.
    """
    try:
        ev = await get_evidence(evidence_id)
    except Exception:
        return 0.0

    if ev.evidence_type in ("MODEL_INFERENCE", "MODEL_SUMMARY"):
        source_llm = getattr(ev, "source_llm", None)
        if source_llm:
            return 0.4
        return 0.0

    return float(ev.quality_score)


# ---------------------------------------------------------------------------
# §5.8 Confidence recomputation
# ---------------------------------------------------------------------------

async def recompute_hypothesis_confidence(
    hypothesis_id: str,
    cause_event_id: str,
) -> Optional[HypothesisVersion]:
    """Recompute confidence from the current evidence graph.

    Formula:
        For every claim -> hypothesis edge (relation SUPPORTS or CONTRADICTS):
            a SUPPORTS edge contributes claim.confidence × group-strength to
            support_score (supporting evidence) and contra_score
            (contradicting evidence) exactly as before;
            a CONTRADICTS edge INVERTS the polarity — the claim's supporting
            evidence now counts against the hypothesis and vice versa
            (merge spec Part 1 §5.2 P2 / Correction 6: the old loop treated
            every claim edge as SUPPORTS, so a CONTRADICTS claim silently
            RAISED confidence).
        For every evidence -> hypothesis edge (direct edges, new in P2):
            support_score += edge.weight × _evidence_weight(evidence) for
            SUPPORTS; contra_score += same for CONTRADICTS.
        raw = support_score - contra_score
        new_confidence = 1 / (1 + exp(-raw))  # sigmoid, bounded [0, 1]

    Preconditions: hypothesis_id exists.
    Postconditions:
        If abs(new_confidence - current.confidence) < 0.01 -> returns None,
        no new version (None is permitted by this function's contract).
        Else -> returns a new HypothesisVersion.
    Raises:
        HypothesisNotFoundError.
        DatabaseUnavailableError.
    Side effects:
        If a new version is created: same side effects as update_hypothesis.
    """
    # ICE flag (spec §9 / §19.4; benchmark baseline B5 toggles it). Read at
    # CALL time — audit gap #39. When disabled, this is a stub returning
    # None (the function's existing "no material change" contract value):
    # no version rows are created and confidences stay frozen at their
    # creation-time values, which is exactly what "engine without
    # versioning" means for the B5 baseline.
    if os.getenv("ENABLE_HYPOTHESIS_VERSIONING", "true").lower() != "true":
        return None

    current = await get_current_version(hypothesis_id)
    hypothesis = await get_hypothesis(hypothesis_id)

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot recompute: no DB pool")

    async with pool.acquire() as conn:
        claim_rows = await conn.fetch(
            """
            SELECT c.claim_id, c.confidence,
                   c.supporting_evidence_ids, c.contradicting_evidence_ids,
                   e.relation
            FROM claims c
            JOIN evidence_graph_edges e ON e.from_node = c.claim_id
            WHERE e.to_node = $1
              AND e.from_type = 'claim'
              AND e.relation IN ('SUPPORTS', 'CONTRADICTS')
            """,
            hypothesis_id,
        )
        # NEW (merge spec Part 1 §5.2 P2 / improvement I2): direct
        # evidence -> hypothesis edges, previously ignored by this query —
        # the query below gathers the evidence that supports or contradicts
        # a hypothesis without an intermediate claim.
        direct_ev_rows = await conn.fetch(
            """
            SELECT e.from_node AS evidence_id, e.relation, e.weight
            FROM evidence_graph_edges e
            WHERE e.to_node = $1
              AND e.from_type = 'evidence'
              AND e.relation IN ('SUPPORTS', 'CONTRADICTS')
            """,
            hypothesis_id,
        )

    support_score = 0.0
    contra_score = 0.0
    supporting_evidence_ids: set[str] = set()
    contradicting_evidence_ids: set[str] = set()

    for claim_row in claim_rows:
        rel = claim_row["relation"]
        supp_ev_ids = list(claim_row["supporting_evidence_ids"] or [])
        contra_ev_ids = list(claim_row["contradicting_evidence_ids"] or [])

        if rel == "SUPPORTS":
            for ev_id in supp_ev_ids:
                supporting_evidence_ids.add(ev_id)
            for ev_id in contra_ev_ids:
                contradicting_evidence_ids.add(ev_id)
            support_groups = await _independent_lineage_groups(supp_ev_ids)
            for group in support_groups:
                group_strength = 0.0
                for ev_id in group:
                    group_strength += await _evidence_weight(ev_id)
                support_score += claim_row["confidence"] * min(1.0, group_strength)
            contra_groups = await _independent_lineage_groups(contra_ev_ids)
            for group in contra_groups:
                group_strength = 0.0
                for ev_id in group:
                    group_strength += await _evidence_weight(ev_id)
                contra_score += claim_row["confidence"] * min(1.0, group_strength)
        elif rel == "CONTRADICTS":
            # A claim that CONTRADICTS this hypothesis inverts the polarity
            # of its supporting and contradicting evidence (P2 / Correction 6).
            for ev_id in supp_ev_ids:
                contradicting_evidence_ids.add(ev_id)
            for ev_id in contra_ev_ids:
                supporting_evidence_ids.add(ev_id)
            contra_groups = await _independent_lineage_groups(supp_ev_ids)
            for group in contra_groups:
                group_strength = 0.0
                for ev_id in group:
                    group_strength += await _evidence_weight(ev_id)
                contra_score += claim_row["confidence"] * min(1.0, group_strength)
            support_groups = await _independent_lineage_groups(contra_ev_ids)
            for group in support_groups:
                group_strength = 0.0
                for ev_id in group:
                    group_strength += await _evidence_weight(ev_id)
                support_score += claim_row["confidence"] * min(1.0, group_strength)

    # NEW (P2 / I2): direct evidence -> hypothesis edges merge into the
    # same scores, weighted by edge weight × evidence quality.
    for row in direct_ev_rows:
        ev_id = row["evidence_id"]
        rel = row["relation"]
        w = float(row["weight"] if row["weight"] is not None else 1.0)
        ev_w = await _evidence_weight(ev_id)
        if rel == "SUPPORTS":
            supporting_evidence_ids.add(ev_id)
            support_score += w * ev_w
        elif rel == "CONTRADICTS":
            contradicting_evidence_ids.add(ev_id)
            contra_score += w * ev_w

    raw = support_score - contra_score
    new_confidence = 1.0 / (1.0 + math.exp(-raw))

    if abs(new_confidence - current.confidence) < 0.01:
        return None

    return await update_hypothesis(
        hypothesis_id=hypothesis_id,
        new_confidence=new_confidence,
        reason=(
            f"Recomputed from {len(supporting_evidence_ids)} supporting and "
            f"{len(contradicting_evidence_ids)} contradicting evidence items"
        ),
        cause_event_id=cause_event_id,
        added_supporting_evidence=list(supporting_evidence_ids),
        added_contradicting_evidence=list(contradicting_evidence_ids),
    )


# ---------------------------------------------------------------------------
# §5.9 Lifecycle status
# ---------------------------------------------------------------------------

async def set_hypothesis_status(
    hypothesis_id: str,
    new_status: HypothesisStatus,
    reason: str,
) -> Hypothesis:
    """Change hypothesis lifecycle status.

    Emits hypothesis_status_changed.
    """
    allowed = {
        "PROPOSED",
        "SUPPORTED",
        "CHALLENGED",
        "SURVIVED",
        "FALSIFIED",
        "CONFIRMED",
        "REMAINS_OPEN",
    }
    if new_status not in allowed:
        raise ValueError(f"Invalid status: {new_status}")

    current = await get_hypothesis(hypothesis_id)
    if current.status == new_status:
        return current

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot update hypothesis: no DB pool")

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE hypotheses SET status = $1 WHERE hypothesis_id = $2",
            new_status,
            hypothesis_id,
        )

    await _emit_event(
        current.case_id,
        "hypothesis_status_changed",
        {
            "hypothesis_id": hypothesis_id,
            "old_status": current.status,
            "new_status": new_status,
            "reason": reason,
        },
    )
    return await get_hypothesis(hypothesis_id)


# ---------------------------------------------------------------------------
# §5.11 Lifecycle auto-transitions (Part 2 §7.1, fix 2F)
# ---------------------------------------------------------------------------

async def update_hypothesis_lifecycle(
    hypothesis_id: str,
    current_turn: int,
) -> Optional[str]:
    """Auto-transition hypothesis status based on confidence and contradictions.

    Rules (first match wins, but transitions do not chain — each call
    results in at most one status change):
    - CONFIRMED or FALSIFIED: terminal, no transition.
    - contra > support + 0.5: transition to FALSIFIED (via
      falsification.check_hypothesis_falsified, which sets the status
      itself).
    - Has active contradiction on a supporting claim: transition to
      CHALLENGED (from PROPOSED or SUPPORTED only).
    - confidence >= 0.7 and status is PROPOSED: transition to SUPPORTED.
    - confidence < 0.3 and status is SUPPORTED or CHALLENGED:
      transition to REMAINS_OPEN.

    Returns the new status if a transition occurred, else None.

    HONEST NOTE (doc Part 2 §12 shortcut #3, design note §7.3): the 0.7
    threshold for SUPPORTED, 0.3 for REMAINS_OPEN, and 0.5 falsification
    margin are heuristics — not calibrated against any observed data.
    They will likely need tuning based on real case trajectories.

    Side effects:
        set_hypothesis_status (UPDATE hypotheses + hypothesis_status_changed
        event) when a transition fires; check_hypothesis_falsified may set
        FALSIFIED (its own event) on the falsification branch.
    """
    current = await get_hypothesis(hypothesis_id)
    if current.status in ("CONFIRMED", "FALSIFIED"):
        return None  # terminal

    version = await get_current_version(hypothesis_id)
    confidence = version.confidence

    # Falsification check (uses the existing function from falsification.py;
    # imported lazily — falsification imports this module at top level).
    from case_engine.falsification import check_hypothesis_falsified
    try:
        falsified, reason = await check_hypothesis_falsified(hypothesis_id, margin=0.5)
    except Exception:
        falsified, reason = False, None
    if falsified:
        return "FALSIFIED"

    # Contradiction check on supporting claims (lazy imports: both modules
    # sit above hypothesis.py in the import graph).
    from case_engine.contradiction import find_contradictions_for_claim
    from case_engine.claim import get_claims_for_hypothesis
    has_active_contradiction = False
    try:
        for claim in await get_claims_for_hypothesis(hypothesis_id):
            contras = await find_contradictions_for_claim(claim.claim_id)
            if any(c.status == "active" for c in contras):
                has_active_contradiction = True
                break
    except Exception:
        pass

    if has_active_contradiction and current.status in ("PROPOSED", "SUPPORTED"):
        await set_hypothesis_status(
            hypothesis_id, "CHALLENGED",
            f"Active contradiction detected at turn {current_turn}",
        )
        return "CHALLENGED"

    if confidence >= 0.7 and current.status == "PROPOSED":
        await set_hypothesis_status(
            hypothesis_id, "SUPPORTED",
            f"Confidence {confidence:.2f} >= 0.7",
        )
        return "SUPPORTED"

    if confidence < 0.3 and current.status in ("SUPPORTED", "CHALLENGED"):
        await set_hypothesis_status(
            hypothesis_id, "REMAINS_OPEN",
            f"Confidence {confidence:.2f} < 0.3",
        )
        return "REMAINS_OPEN"

    return None


# ---------------------------------------------------------------------------
# §5.10 Listing
# ---------------------------------------------------------------------------

async def list_hypotheses_for_case(
    case_id: str,
    status: Optional[HypothesisStatus] = None,
) -> list[Hypothesis]:
    """Return hypotheses for a case, optionally filtered by status."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot list hypotheses: no DB pool")
    async with pool.acquire() as conn:
        if status is None:
            rows = await conn.fetch(
                "SELECT * FROM hypotheses WHERE case_id = $1 ORDER BY created_at ASC",
                case_id,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT * FROM hypotheses
                WHERE case_id = $1 AND status = $2
                ORDER BY created_at ASC
                """,
                case_id,
                status,
            )
    return [_row_to_hypothesis(row) for row in rows]