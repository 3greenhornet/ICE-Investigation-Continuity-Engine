"""case_engine/provenance.py — source lineage and traceability metrics.

Owner: investigation logic layer (Part 3, section 5).
Purpose: load sources, score a source's independence from its lineage
(parent chain depth), and compute the Evidence Trace Coverage (ETC) for a
case -- the fraction of claims whose supporting evidence is traceable to a
source with a URI or non-empty metadata.

Downstream dependencies (allowed):
    - db.connection.get_pool
    - case_engine.exceptions (typed errors)
    - case_engine.schemas (Source)
    - case_engine.evidence (public get_evidence; the spec-mandated
      ``_row_to_source`` row mapper is reused here per Part 3 section 5.1)
    - case_engine.claim (public: get_claim)
"""

import logging
from typing import Optional

from db.connection import get_pool
from case_engine.exceptions import DatabaseUnavailableError
from case_engine.schemas import Source
from case_engine.evidence import _row_to_source, get_evidence
from case_engine.claim import get_claim

logger = logging.getLogger(__name__)


async def get_source(source_id: str) -> Source:
    """Load a source by ID. Raises EvidenceNotFoundError if missing.

    Raises:
        EvidenceNotFoundError when the source_id is unknown.
        DatabaseUnavailableError when no DB pool is available.
    Side effects: none.
    """
    from case_engine.exceptions import EvidenceNotFoundError
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot load source: no DB pool")
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM sources WHERE source_id = $1", source_id)
    if row is None:
        raise EvidenceNotFoundError(f"Source not found: {source_id}")
    return _row_to_source(row)


async def compute_independence_score(source_id: str, case_id: str) -> float:
    """Return a 0-1 score indicating independence from other sources in the case.

    Rules:
        - Root source (parent_source_id is None): 1.0
        - Walk up the parent chain counting hops (depth cap 5, cycle-safe).
        - Depth 1 (direct copy): 0.3
        - Depth 2: 0.15 (0.3 / 2)
        - Depth n >= 3: 0.05
        - Source not found: 0.5
        - No DB pool: 0.5
    Side effects: none. (The score is returned, not persisted; callers may
    store it on the source row if desired.)
    """
    pool = await get_pool()
    if pool is None:
        return 0.5

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT parent_source_id FROM sources WHERE source_id = $1 AND case_id = $2",
            source_id, case_id,
        )
    if row is None:
        return 0.5
    if row["parent_source_id"] is None:
        return 1.0

    depth = 0
    current = row["parent_source_id"]
    visited: set[str] = set()
    while current and current not in visited:
        visited.add(current)
        depth += 1
        if depth > 5:
            break
        async with pool.acquire() as conn:
            next_row = await conn.fetchrow(
                "SELECT parent_source_id FROM sources WHERE source_id = $1", current
            )
        if not next_row:
            break
        current = next_row["parent_source_id"]

    if depth == 1:
        return 0.3
    if depth == 2:
        return 0.15
    return 0.05


async def compute_evidence_trace_coverage(case_id: str) -> float:
    """Return the fraction of claims whose supporting evidence is traceable.

    A claim is "traceable" iff:
        - supporting_evidence_ids is non-empty, AND
        - at least one of those evidence items has a source whose source_uri
          is non-NULL OR whose metadata is non-empty (differs from '{}').

    Returns 0.0 when the case has no claims.
    Raises: DatabaseUnavailableError.
    Side effects: none.
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot compute ETC: no DB pool")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT claim_id, supporting_evidence_ids FROM claims WHERE case_id = $1",
            case_id,
        )

    if not rows:
        return 0.0

    traceable = 0
    for row in rows:
        evidence_ids = row["supporting_evidence_ids"] or []
        if not evidence_ids:
            continue
        async with pool.acquire() as conn:
            ev_row = await conn.fetchrow(
                """
                SELECT 1 FROM evidence e
                JOIN sources s ON e.source_id = s.source_id
                WHERE e.evidence_id = ANY($1::text[])
                  AND (s.source_uri IS NOT NULL OR s.metadata <> '{}'::jsonb)
                LIMIT 1
                """,
                evidence_ids,
            )
        if ev_row:
            traceable += 1

    return traceable / len(rows)