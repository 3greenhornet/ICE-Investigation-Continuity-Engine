"""case_engine/information_gap.py — explicit uncertainty as tracked gaps.

Owner: investigation logic layer (Part 3, section 2).
Purpose: create, list, prioritize, and resolve information gaps. A gap is an
explicit statement of missing evidence, tagged with the hypotheses it would
discriminate. Priority is the normalized entropy of the discriminating
hypotheses' current confidences (a "planning belief" heuristic in [0, 1],
per Part 3 section 0 rule 14 -- not a calibrated probability).

Downstream dependencies (allowed):
    - db.connection.get_pool
    - case_engine._emit_event
    - case_engine.exceptions (typed errors)
    - case_engine.schemas (InformationGap)
    - case_engine.hypothesis (public: get_current_version)
    - case_engine.evidence (public: get_evidence -- imported locally in
      resolve_gap to keep the module import graph lazy)
"""

import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Optional

from db.connection import get_pool
from case_engine import _emit_event
from case_engine.exceptions import (
    DatabaseUnavailableError, GapNotFoundError, HypothesisNotFoundError
)
from case_engine.schemas import InformationGap
from case_engine.hypothesis import get_current_version

logger = logging.getLogger(__name__)


def _row_to_gap(row) -> InformationGap:
    """Map an asyncpg Record from ``information_gaps`` to an InformationGap model."""
    return InformationGap(
        gap_id=row["gap_id"],
        case_id=row["case_id"],
        needed_evidence=row["needed_evidence"],
        current_state=row["current_state"],
        why_needed=row["why_needed"],
        priority=row["priority"],
        discriminating_hypothesis_ids=list(row["discriminating_hypothesis_ids"] or []),
        depends_on_gap_ids=list(row["depends_on_gap_ids"] or []),
        blocks_action_ids=list(row["blocks_action_ids"] or []),
        created_at=row["created_at"],
        resolved_by_evidence_ids=list(row["resolved_by_evidence_ids"] or []),
        resolved_at=row["resolved_at"],
    )


async def create_gap(
    case_id: str,
    needed_evidence: str,
    why_needed: str,
    discriminating_hypothesis_ids: list[str],
    priority: float = 0.5,
) -> InformationGap:
    """Create an information gap.

    Preconditions:
        case_id exists.
        needed_evidence is non-empty.
        0.0 <= priority <= 1.0.
    Postconditions:
        information_gaps row created.
        information_gap_created event emitted.
    Raises: ValueError, DatabaseUnavailableError.
    Side effects:
        INSERT INTO information_gaps (1).
        _emit_event("information_gap_created", {...}).
    """
    if not needed_evidence or not needed_evidence.strip():
        raise ValueError("needed_evidence must be non-empty")
    if not (0.0 <= priority <= 1.0):
        raise ValueError("priority must be in [0, 1]")

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot create gap: no DB pool")

    gap_id = f"gap_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO information_gaps (
                gap_id, case_id, needed_evidence, why_needed, priority,
                discriminating_hypothesis_ids, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6::text[], $7)
            """,
            gap_id, case_id, needed_evidence.strip(), why_needed.strip(),
            priority, discriminating_hypothesis_ids or [], now,
        )

    await _emit_event(case_id, "information_gap_created", {
        "gap_id": gap_id,
        "needed_evidence": needed_evidence.strip(),
        "priority": priority,
    })

    return await get_gap(gap_id)


async def get_gap(gap_id: str) -> InformationGap:
    """Load a gap by ID. Raises GapNotFoundError when missing."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot load gap: no DB pool")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM information_gaps WHERE gap_id = $1", gap_id
        )
    if row is None:
        raise GapNotFoundError(f"Gap not found: {gap_id}")
    return _row_to_gap(row)


async def list_gaps_for_case(
    case_id: str,
    resolved: bool = False,
) -> list[InformationGap]:
    """List unresolved (default) or resolved gaps, ordered by priority DESC."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot list gaps: no DB pool")
    async with pool.acquire() as conn:
        if resolved:
            rows = await conn.fetch(
                """
                SELECT * FROM information_gaps
                WHERE case_id = $1 AND resolved_at IS NOT NULL
                ORDER BY priority DESC
                """,
                case_id,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT * FROM information_gaps
                WHERE case_id = $1 AND resolved_at IS NULL
                ORDER BY priority DESC
                """,
                case_id,
            )
    return [_row_to_gap(row) for row in rows]


async def find_gaps_for_hypothesis(hypothesis_id: str) -> list[InformationGap]:
    """Return unresolved gaps whose discriminating set includes hypothesis_id."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot list gaps: no DB pool")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM information_gaps
            WHERE $1 = ANY(discriminating_hypothesis_ids)
              AND resolved_at IS NULL
            ORDER BY priority DESC
            """,
            hypothesis_id,
        )
    return [_row_to_gap(row) for row in rows]


async def compute_gap_priority(gap_id: str) -> float:
    """Recompute a gap's priority as the normalized entropy of the
    hypotheses it discriminates.

    Preconditions: gap_id exists.
    Postconditions:
        Returns a float in [0.0, 1.0].
        If the gap discriminates fewer than 2 hypotheses, returns 0.3.
    Raises: GapNotFoundError, DatabaseUnavailableError.
    Side effects: none. (Caller must persist if desired; there is no auto-persist.)

    Formula:
        confidences = [get_current_version(h).confidence for h in discriminating_ids]
        probs = confidences / sum(confidences)
        H = -sum(p * log(p + 1e-9))
        H_max = log(len(probs))
        return H / H_max (0.3 if H_max == 0 or fewer than 2 hypotheses)

    Note (Part 3 section 0 rule 14): the confidences are planning beliefs,
    normalized to sum to 1 within the hypothesis set before entropy is
    computed. The result is a heuristic in [0, 1].
    """
    gap = await get_gap(gap_id)

    if len(gap.discriminating_hypothesis_ids) < 2:
        return 0.3

    confidences: list[float] = []
    for h_id in gap.discriminating_hypothesis_ids:
        try:
            version = await get_current_version(h_id)
            confidences.append(version.confidence)
        except HypothesisNotFoundError:
            continue

    if len(confidences) < 2:
        return 0.3

    total = sum(confidences)
    if total <= 0.0:
        return 0.0
    probs = [c / total for c in confidences]

    H = -sum(p * math.log(p + 1e-9) for p in probs)
    H_max = math.log(len(probs))
    if H_max <= 0.0:
        return 0.3

    return max(0.0, min(1.0, H / H_max))


async def resolve_gap(
    gap_id: str,
    resolved_by_evidence_id: str,
) -> InformationGap:
    """Mark a gap resolved by evidence.

    Preconditions:
        gap_id exists.
        resolved_by_evidence_id exists and belongs to the same case.
    Postconditions:
        resolved_by_evidence_ids contains the evidence_id (appended at most
        once -- GAP FIX, reviewer audit #27: the append is idempotent).
        resolved_at == now.
        information_gap_resolved event emitted.
    Raises: GapNotFoundError, EvidenceNotFoundError, ValueError (cross-case),
        DatabaseUnavailableError.
    Side effects:
        UPDATE information_gaps (1).
        _emit_event("information_gap_resolved", {...}).
    """
    from case_engine.evidence import get_evidence

    gap = await get_gap(gap_id)
    evidence = await get_evidence(resolved_by_evidence_id)
    if evidence.case_id != gap.case_id:
        raise ValueError("Resolution evidence belongs to a different case")

    now = datetime.now(timezone.utc)
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot resolve gap: no DB pool")

    async with pool.acquire() as conn:
        # GAP FIX (audit #27): idempotent append. Only append the evidence id
        # when it is NOT already in resolved_by_evidence_ids, so repeated
        # calls with the same evidence never duplicate the entry.
        await conn.execute(
            """
            UPDATE information_gaps
            SET resolved_by_evidence_ids = array_append(resolved_by_evidence_ids, $1),
                resolved_at = $2
            WHERE gap_id = $3 AND NOT ($1 = ANY(resolved_by_evidence_ids))
            """,
            resolved_by_evidence_id, now, gap_id,
        )
        # resolved_at is ALWAYS set, even on an idempotent re-resolution where
        # the append was skipped.
        await conn.execute(
            """
            UPDATE information_gaps
            SET resolved_at = $1
            WHERE gap_id = $2 AND resolved_at IS NULL
            """,
            now, gap_id,
        )

    await _emit_event(gap.case_id, "information_gap_resolved", {
        "gap_id": gap_id,
        "resolved_by_evidence_id": resolved_by_evidence_id,
    })

    return await get_gap(gap_id)