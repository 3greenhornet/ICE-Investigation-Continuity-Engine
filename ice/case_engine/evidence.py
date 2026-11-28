"""case_engine/evidence.py — evidence plane of the ICE case engine.

Owner: ICE revamp Part 2 §3 (implementation task ice-2-b).
Purpose: single entry point for all evidence. Enforces the ingestion boundary,
classifies evidence type, detects duplicates, and resolves source lineage.

Downstream deps:
    - case_engine.claim (extract_claims reads evidence via get_evidence)
    - case_engine.hypothesis (_evidence_weight reads evidence via get_evidence)
    - case_engine.contradiction / information_gap / evidence_graph / action (Part 3)
    - app routes (Part 4) and scripts/verify_part2.py

Upstream deps (owned by ice-2-a):
    - db.connection.get_pool
    - case_engine._emit_event
    - case_engine.exceptions (CaseNotFoundError, DatabaseUnavailableError,
      EvidenceNotFoundError)
    - case_engine.schemas (Evidence, Source, EvidenceType, EvidenceStatus,
      SourceType)
    - engine.memory.embed (substrate embedding function)
    - db/migrations/012_case_model.sql (sources / evidence tables, incl. the
      evidence.metadata JSONB column)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from db.connection import get_pool
from case_engine import _emit_event
from case_engine.exceptions import (
    CaseNotFoundError,  # noqa: F401  (re-exported for transitive raising)
    DatabaseUnavailableError,
    EvidenceNotFoundError,
)
from case_engine.schemas import (
    Evidence,
    Source,
    EvidenceType,
    EvidenceStatus,
    SourceType,
)
from engine.memory import embed  # substrate

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a hard runtime dep
    import asyncpg

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# §3.3 Evidence type classification patterns
# ---------------------------------------------------------------------------

EVIDENCE_TYPE_PATTERNS: dict[str, list[str]] = {
    "MEASUREMENT": [
        r"\d+(\.\d+)?\s*(ms|s|kb|mb|gb|%|rpm|qps|iops)",
        r"(latency|throughput|cpu|memory|error rate)\s*[:=]\s*\d+",
        r"p\d{2}\s*[:=]\s*\d+",
    ],
    "OBSERVATION": [
        r"\b(i|we|they)\s+(saw|observed|noticed|found)\b",
        r"\b(appears|seems|looks like)\b",
    ],
    "DOCUMENT_CLAIM": [
        r"according to",
        r"the\s+(docs|documentation|manual|spec)\s+says",
        r"states that",
    ],
    "HUMAN_ASSERTION": [
        r"the\s+(engineer|user|participant)\s+(said|claimed|reported)",
        r"\b(?:john|jane|alice|bob|sam|alex)\s+said\b",
    ],
    "TOOL_RESULT": [
        r"^(git|kubectl|docker|psql|curl)\s+\w+",
        r"^(output|result|response):",
    ],
    "EXPERIMENT_RESULT": [
        r"\b(test|experiment|probe)\s+(passed|failed|showed|returned)\b",
        r"\bwe ran\b",
    ],
    "MODEL_INFERENCE": [
        r"^(the model|the llm|the ai)\s+(thinks|believes|infers|concludes)",
        r"^(this|that) suggests",
    ],
    "MODEL_SUMMARY": [
        r"^(in summary|to summarize|summary:)",
    ],
}

# Type-aware quality floors (ported from the Oct 23 codebase, merge spec
# Part 1 §5.1 / improvement I1). With source scores at their 0.5 defaults,
# the old authority × directness × freshness product gave every evidence
# item 0.125 — the multiplier on every _evidence_weight call, i.e. a 7.6×
# weaker confidence signal per item than MEASUREMENT's 0.95 floor.
DEFAULT_QUALITY_SCORES: dict[EvidenceType, float] = {
    "MEASUREMENT": 0.95,
    "TOOL_RESULT": 0.90,
    "EXPERIMENT_RESULT": 0.85,
    "DOCUMENT_CLAIM": 0.70,
    "OBSERVATION": 0.65,
    "HUMAN_ASSERTION": 0.50,
    "MODEL_SUMMARY": 0.40,
    "MODEL_INFERENCE": 0.0,
}

# Hari ICE improvements pass — how much of a type's quality floor a source
# FAMILY can claim (see ingest_evidence). First-party producers of machine
# observations (TOOL/DATABASE/LOG_FILE) keep the full floor; a human
# reporting what they saw keeps most of it; documents and URLs reporting
# numbers second-hand are capped at DOCUMENT_CLAIM's own floor (0.70) —
# the second-pass audit's exact example (MEASUREMENT floor 0.95 vs
# TOOL_RESULT 0.90 conflating content format with reliability).
_SOURCE_QUALITY_CAPS: dict[str, float] = {
    "TOOL": 1.00,
    "DATABASE": 1.00,
    "LOG_FILE": 1.00,
    "HUMAN": 0.90,
    "CODE_REPO": 0.80,
    "URL": 0.80,
    "DOCUMENT": 0.75,
}


def classify_evidence_type(content: str) -> EvidenceType:
    """Classify evidence type by pattern match (sync by design).

    Preconditions: content is a string (may be empty).
    Postconditions: returns an EvidenceType from Part 1 §6.2.
    Raises: nothing.
    Side effects: none.

    Rule: pattern matching is case-insensitive and applied in the order
    listed in EVIDENCE_TYPE_PATTERNS. First match wins. Fallback is OBSERVATION.
    """
    if not content:
        return "OBSERVATION"
    content_lower = content.lower()
    for evidence_type, patterns in EVIDENCE_TYPE_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, content_lower, re.IGNORECASE | re.MULTILINE):
                return evidence_type  # type: ignore[return-value]
    return "OBSERVATION"


# ---------------------------------------------------------------------------
# §3.5 Row mapping
# ---------------------------------------------------------------------------

def _parse_jsonb_or_empty(raw):
    """Defensively decode a jsonb column (merge spec Part 1 §5.3 P3 / I3+I6).

    asyncpg's default codec returns a dict, but a JSONB->TEXT migration or a
    custom codec can hand back a raw JSON string; either way we return a dict
    (or {} when undecodable / not dict-shaped) instead of crashing.
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


def _row_to_evidence(row: asyncpg.Record) -> Evidence:
    """Map an asyncpg row to Evidence. Explicit column mapping."""
    embedding_raw = row["embedding"]
    embedding: Optional[list[float]] = None
    if embedding_raw is not None:
        try:
            import numpy as np

            embedding = np.asarray(embedding_raw, dtype=np.float32).tolist()
        except Exception:
            embedding = None

    return Evidence(
        evidence_id=row["evidence_id"],
        case_id=row["case_id"],
        source_id=row["source_id"],
        evidence_type=row["evidence_type"],
        content=row["content"],
        embedding=embedding,
        observed_at=row["observed_at"],
        recorded_at=row["recorded_at"],
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        supersedes=row["supersedes"],
        superseded_by=row["superseded_by"],
        source_lineage_id=row["source_lineage_id"],
        duplicate_cluster_id=row["duplicate_cluster_id"],
        quality_score=row["quality_score"],
        status=row["status"],
        extracted_claim_ids=list(row["extracted_claim_ids"] or []),
        # P3/I6: guard against the column being absent mid-migration AND
        # against jsonb codecs that return a string instead of a dict.
        metadata=(
            _parse_jsonb_or_empty(row["metadata"])
            if "metadata" in row.keys()
            else {}
        ),
        # Part 2 §1.2: same mid-migration tolerance for migration 015's
        # source_llm column (asyncpg Record supports .keys()).
        source_llm=(
            row["source_llm"] if "source_llm" in row.keys() else None
        ),
    )


def _row_to_source(row: asyncpg.Record) -> Source:
    """Map an asyncpg row to Source. Explicit column mapping."""
    return Source(
        source_id=row["source_id"],
        case_id=row["case_id"],
        source_type=row["source_type"],
        source_uri=row["source_uri"],
        source_hash=row["source_hash"],
        parent_source_id=row["parent_source_id"],
        lineage_id=row["lineage_id"],
        duplicate_cluster_id=row["duplicate_cluster_id"],
        authority_score=row["authority_score"],
        directness_score=row["directness_score"],
        freshness_score=row["freshness_score"],
        independence_score=row["independence_score"],
        metadata=(
            _parse_jsonb_or_empty(row["metadata"])
            if "metadata" in row.keys()
            else {}
        ),
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# §3.6 Source creation
# ---------------------------------------------------------------------------

async def _create_source(
    case_id: str,
    source_type: SourceType,
    source_uri: Optional[str],
    metadata: Optional[dict] = None,
    parent_source_id: Optional[str] = None,
    authority_score: float = 0.5,
    directness_score: float = 0.5,
    freshness_score: float = 0.5,
) -> Source:
    """Create a Source row.

    Preconditions: case_id exists.
    Postconditions:
        A sources row exists with a fresh source_id.
        If parent_source_id is None, lineage_id == source_id.
        Otherwise lineage_id == parent's lineage_id (or parent's source_id).
        source_hash = sha256 of source_uri if provided, else None.
        authority/directness/freshness scores are stored as given — the
        schema defaults of 0.5 apply when the caller has no source
        knowledge (audit gap #24: these columns were previously dead;
        callers that DO know their source — e.g. a curated ingest or a CLI
        `--authority` flag — can now make quality_score meaningful: the
        source product feeds ingest_evidence's max(source_quality,
        type_default) computation).
    Raises: DatabaseUnavailableError, ValueError on out-of-range scores.
    Side effects: INSERT INTO sources (1 row).
    """
    for name, value in (
        ("authority_score", authority_score),
        ("directness_score", directness_score),
        ("freshness_score", freshness_score),
    ):
        if not (0.0 <= float(value) <= 1.0):
            raise ValueError(f"{name} must be in [0, 1]")

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot create source: no DB pool")

    source_id = f"src_{uuid.uuid4().hex[:12]}"
    source_hash = (
        hashlib.sha256(source_uri.encode("utf-8")).hexdigest()
        if source_uri
        else None
    )
    now = datetime.now(timezone.utc)

    if parent_source_id is None:
        lineage_id = source_id
    else:
        async with pool.acquire() as conn:
            parent = await conn.fetchrow(
                "SELECT lineage_id, source_id FROM sources WHERE source_id = $1",
                parent_source_id,
            )
        lineage_id = (
            parent["lineage_id"]
            if parent and parent["lineage_id"]
            else parent_source_id
        )

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sources (
                source_id, case_id, source_type, source_uri, source_hash,
                parent_source_id, lineage_id, metadata,
                authority_score, directness_score, freshness_score,
                created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12)
            """,
            source_id,
            case_id,
            source_type,
            source_uri,
            source_hash,
            parent_source_id,
            lineage_id,
            json.dumps(metadata or {}),
            float(authority_score),
            float(directness_score),
            float(freshness_score),
            now,
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM sources WHERE source_id = $1", source_id
        )
    return _row_to_source(row)


# ---------------------------------------------------------------------------
# §3.7 Lineage resolution
# ---------------------------------------------------------------------------

async def _resolve_lineage(
    source_id: str,
    parent_source_id: Optional[str],
) -> str:
    """Return the lineage root for a source.

    Preconditions: source_id is a valid source (may be freshly created).
    Postconditions: returns the root source_id.
    Raises: nothing.
    Side effects: none.
    """
    if parent_source_id is None:
        return source_id

    pool = await get_pool()
    if pool is None:
        return source_id

    visited: set[str] = set()
    current: Optional[str] = parent_source_id
    async with pool.acquire() as conn:
        while current and current not in visited:
            visited.add(current)
            row = await conn.fetchrow(
                "SELECT parent_source_id, lineage_id FROM sources WHERE source_id = $1",
                current,
            )
            if not row:
                break
            if row["lineage_id"]:
                return row["lineage_id"]
            if not row["parent_source_id"]:
                return current
            current = row["parent_source_id"]

    return source_id


# ---------------------------------------------------------------------------
# §3.8 Duplicate cluster detection
# ---------------------------------------------------------------------------

async def _detect_duplicate_cluster(
    case_id: str,
    embedding: list[float],
    threshold: float = 0.95,
) -> Optional[str]:
    """Return an existing duplicate_cluster_id if this embedding matches one.

    Preconditions:
        embedding is a non-empty list of floats, dim 768.
        case_id is a valid case.
    Postconditions:
        Returns an existing duplicate_cluster_id, or None.
    Raises: nothing (swallows DB errors, returns None).
    Side effects: none.
    """
    if not embedding:
        return None
    pool = await get_pool()
    if pool is None:
        return None

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT duplicate_cluster_id, 1 - (embedding <=> $1) AS similarity
                FROM evidence
                WHERE case_id = $2
                  AND duplicate_cluster_id IS NOT NULL
                  AND embedding IS NOT NULL
                  AND 1 - (embedding <=> $1) > $3
                ORDER BY similarity DESC
                LIMIT 1
                """,
                embedding,
                case_id,
                threshold,
            )
        return row["duplicate_cluster_id"] if row else None
    except Exception as exc:
        logger.debug("Duplicate detection failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# §3.9 Ingestion (public boundary)
# ---------------------------------------------------------------------------

async def ingest_evidence(
    case_id: str,
    content: str,
    source_id: str,
    evidence_type: Optional[EvidenceType] = None,
    observed_at: Optional[datetime] = None,
    valid_from: Optional[datetime] = None,
    valid_until: Optional[datetime] = None,
    quality_score: Optional[float] = None,
    metadata: Optional[dict] = None,
) -> Evidence:
    """Ingest one piece of evidence.

    Preconditions:
        case_id exists.
        content is non-empty.
        source_id exists and belongs to case_id.
        If evidence_type is None, it is auto-classified.
    Postconditions:
        evidence row exists with embedding computed from content (best effort).
        duplicate_cluster_id is set if a near-duplicate exists, else a fresh
        cluster id.
        source_lineage_id is set from the source's lineage.
        quality_score (merge spec Part 1 §5.1, improvement I1):
            quality_score argument (if given) is clamped to [0, 1] and used
            as-is; otherwise it is max(source_quality, type_default) where
            source_quality = authority × directness × freshness of the
            source and type_default = DEFAULT_QUALITY_SCORES[type]. The
            type floor keeps MEASUREMENT/TOOL_RESULT evidence from being
            depressed by 0.5-default source scores (the old 0.125 problem),
            while the source product still differentiates a curated source
            (fin-1's caller-controllable scores) from a default one.
        metadata is persisted on the evidence row (audit gap #1).
        evidence_ingested event emitted.
    Raises:
        ValueError: if content is empty, or the source belongs to another case.
        DatabaseUnavailableError.
        EvidenceNotFoundError: if the source does not exist.
        CaseNotFoundError: (transitive, via downstream validation).
    Side effects:
        INSERT INTO evidence (1 row).
        _emit_event("evidence_ingested", {...}).
    """
    if not content or not content.strip():
        raise ValueError("content must be non-empty")

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot ingest evidence: no DB pool")

    # Validate source ownership (and pull source scores for quality_score).
    # Hari ICE improvements pass: source_type is also pulled — the
    # source-aware quality caps in ingest need it (a DOCUMENT reporting
    # "95%" is not a first-party MEASUREMENT).
    async with pool.acquire() as conn:
        source_row = await conn.fetchrow(
            """
            SELECT case_id, lineage_id, source_type,
                   authority_score, directness_score, freshness_score
            FROM sources WHERE source_id = $1
            """,
            source_id,
        )
    if not source_row:
        raise EvidenceNotFoundError(f"Source not found: {source_id}")
    if source_row["case_id"] != case_id:
        raise ValueError(f"Source {source_id} does not belong to case {case_id}")

    # Classify
    resolved_type: EvidenceType = evidence_type or classify_evidence_type(content)

    # Embed (best effort; NULL on failure)
    embedding: Optional[list[float]] = None
    try:
        embedding = await embed(content)
    except Exception as exc:
        logger.warning("Embedding failed for evidence: %s", exc)

    # Duplicate detection. Hari ICE improvements pass: when embeddings are
    # unavailable (no embedding provider configured) the semantic channel
    # is replaced by an EXACT-normalized-content hash — exact duplicates
    # are still clustered (the verify_part2 duplicate test) while semantic
    # near-duplicates wait for embeddings. Honest downgrade, never a
    # silent no-op.
    cluster_id: Optional[str] = None
    if embedding is not None:
        cluster_id = await _detect_duplicate_cluster(case_id, embedding, threshold=0.95)
    if cluster_id is None:
        # exact normalized-content match (POSIX class: PG regex has no \s)
        norm = re.sub(r"\s+", " ", (content or "").strip().lower())
        async with pool.acquire() as conn:
            prior = await conn.fetchrow(
                """
                SELECT duplicate_cluster_id FROM evidence
                WHERE case_id = $1
                  AND regexp_replace(lower(content), '[[:space:]]+', ' ', 'g')
                      = $2
                  AND duplicate_cluster_id IS NOT NULL
                LIMIT 1
                """,
                case_id, norm,
            )
        if prior is not None:
            cluster_id = prior["duplicate_cluster_id"]
    if cluster_id is None:
        cluster_id = f"dup_{uuid.uuid4().hex[:8]}"

    # quality_score (merge spec Part 1 §5.1 P1 / improvement I1): explicit
    # caller override wins; otherwise max(source_quality, type_default) —
    # source product keeps per-source differentiation (fin-1 audit gap #24
    # made the source scores caller-controllable), type floor fixes the
    # 0.5^3 = 0.125 default-score problem.
    if quality_score is not None:
        final_quality = max(0.0, min(1.0, float(quality_score)))
    else:
        source_quality = round(
            float(source_row["authority_score"])
            * float(source_row["directness_score"])
            * float(source_row["freshness_score"]),
            6,
        )
        type_default = DEFAULT_QUALITY_SCORES.get(resolved_type, 0.5)
        # Hari ICE improvements pass (second-pass audit: "Evidence quality
        # currently confuses what something LOOKS LIKE with how trustworthy
        # it is ... a measurement-looking statement may receive a high
        # quality score even when the underlying source has not been
        # independently verified"). A type floor now applies IN FULL only
        # when the source can actually produce that kind of evidence
        # first-party; second-hand sources get a capped floor. A document
        # REPORTING '95%, 240ms, 4GB' is a document claim about a
        # measurement, not the measurement itself.
        source_cap = _SOURCE_QUALITY_CAPS.get(
            source_row["source_type"], 0.75
        )
        capped_default = min(type_default, source_cap)
        final_quality = max(source_quality, capped_default)

    evidence_id = f"ev_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO evidence (
                evidence_id, case_id, source_id, evidence_type, content, embedding,
                observed_at, recorded_at, valid_from, valid_until,
                source_lineage_id, duplicate_cluster_id, metadata,
                quality_score, status, extracted_claim_ids
            ) VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10,
                $11, $12, $13::jsonb,
                $14, 'ACTIVE', '{}'
            )
            """,
            evidence_id,
            case_id,
            source_id,
            resolved_type,
            content.strip(),
            embedding,
            observed_at,
            now,
            valid_from,
            valid_until,
            source_row["lineage_id"] or source_id,
            cluster_id,
            json.dumps(metadata or {}),
            final_quality,
        )

    await _emit_event(
        case_id,
        "evidence_ingested",
        {
            "evidence_id": evidence_id,
            "evidence_type": resolved_type,
            "source_id": source_id,
        },
    )

    return await get_evidence(evidence_id)


# ---------------------------------------------------------------------------
# §3.10-3.12 Reads
# ---------------------------------------------------------------------------

async def get_evidence(evidence_id: str) -> Evidence:
    """Load evidence by ID. Raises EvidenceNotFoundError if missing."""
    if not evidence_id:
        raise ValueError("evidence_id must be non-empty")
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot load evidence: no DB pool")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evidence WHERE evidence_id = $1", evidence_id
        )
    if row is None:
        raise EvidenceNotFoundError(f"Evidence not found: {evidence_id}")
    return _row_to_evidence(row)


async def get_active_evidence(case_id: str, limit: int = 1000) -> list[Evidence]:
    """Return all ACTIVE evidence for a case ordered by recorded_at DESC."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot list evidence: no DB pool")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM evidence
            WHERE case_id = $1 AND status = 'ACTIVE'
            ORDER BY recorded_at DESC
            LIMIT $2
            """,
            case_id,
            limit,
        )
    return [_row_to_evidence(row) for row in rows]


async def get_stale_or_superseded(case_id: str) -> list[Evidence]:
    """Return evidence whose status is STALE, SUPERSEDED, or DISPUTED."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot list evidence: no DB pool")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM evidence
            WHERE case_id = $1 AND status IN ('STALE', 'SUPERSEDED', 'DISPUTED')
            ORDER BY recorded_at DESC
            """,
            case_id,
        )
    return [_row_to_evidence(row) for row in rows]


# ---------------------------------------------------------------------------
# §3.13-3.14 Status transitions
# ---------------------------------------------------------------------------

async def mark_superseded(
    old_evidence_id: str,
    new_evidence_id: str,
    reason: str,
) -> None:
    """Mark old evidence as superseded by new evidence.

    Preconditions: both IDs exist and belong to the same case.
    Postconditions:
        old.superseded_by == new_evidence_id, status == 'SUPERSEDED'.
        new.supersedes == old_evidence_id.
        evidence_superseded event emitted for the case.
    Raises:
        EvidenceNotFoundError if either is missing.
        ValueError if they belong to different cases.
    Side effects:
        UPDATE evidence (2 rows, one transaction).
        _emit_event("evidence_superseded", {...}).
    """
    old = await get_evidence(old_evidence_id)
    new = await get_evidence(new_evidence_id)
    if old.case_id != new.case_id:
        raise ValueError("Cannot supersede across cases")

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot mark superseded: no DB pool")

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE evidence
                SET superseded_by = $1, status = 'SUPERSEDED'
                WHERE evidence_id = $2
                """,
                new_evidence_id,
                old_evidence_id,
            )
            await conn.execute(
                "UPDATE evidence SET supersedes = $1 WHERE evidence_id = $2",
                old_evidence_id,
                new_evidence_id,
            )

    await _emit_event(
        old.case_id,
        "evidence_superseded",
        {
            "old_evidence_id": old_evidence_id,
            "new_evidence_id": new_evidence_id,
            "reason": reason,
        },
    )


async def update_evidence_status(
    evidence_id: str,
    new_status: EvidenceStatus,
    reason: str,
) -> Evidence:
    """Change evidence status. Emits evidence_status_changed."""
    allowed = {"ACTIVE", "STALE", "SUPERSEDED", "DISPUTED", "RESOLVED", "ARCHIVED"}
    if new_status not in allowed:
        raise ValueError(f"Invalid status: {new_status}")
    current = await get_evidence(evidence_id)
    if current.status == new_status:
        return current

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot update evidence: no DB pool")
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE evidence SET status = $1 WHERE evidence_id = $2",
            new_status,
            evidence_id,
        )

    await _emit_event(
        current.case_id,
        "evidence_status_changed",
        {
            "evidence_id": evidence_id,
            "old_status": current.status,
            "new_status": new_status,
            "reason": reason,
        },
    )
    return await get_evidence(evidence_id)


# ---------------------------------------------------------------------------
# §3.15 Semantic search
# ---------------------------------------------------------------------------

async def find_semantic_duplicates(
    case_id: str,
    content: str,
    embedding: Optional[list[float]] = None,
    threshold: float = 0.92,
    limit: int = 10,
) -> list[Evidence]:
    """Return evidence in the same case with cosine similarity > threshold."""
    if embedding is None:
        try:
            embedding = await embed(content)
        except Exception:
            return []
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot search: no DB pool")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT *, 1 - (embedding <=> $1) AS similarity
            FROM evidence
            WHERE case_id = $2 AND embedding IS NOT NULL
              AND 1 - (embedding <=> $1) > $3
            ORDER BY similarity DESC
            LIMIT $4
            """,
            embedding,
            case_id,
            threshold,
            limit,
        )
    return [_row_to_evidence(row) for row in rows]


# ---------------------------------------------------------------------------
# External LLM analysis ingestion (Part 2 §2.1, Feature 2A)
# ---------------------------------------------------------------------------

def _prompt_hash(llm_name: str, prompt_used: str) -> str:
    """SHA-256 of the normalized (llm, prompt) pair (Part 2 §2.7).

    Normalization is lowercase + strip on both components, so "Claude"
    and "claude" with the same prompt map to the same hash.
    """
    return hashlib.sha256(
        f"{llm_name.strip().lower()}::{prompt_used.strip().lower()}".encode("utf-8")
    ).hexdigest()


async def _check_external_analysis_duplicate(
    case_id: str,
    llm_name: str,
    prompt_used: str,
) -> Optional[str]:
    """Check if an identical (LLM, prompt) analysis already exists.

    Part 2 §2.7 (honesty shortcut #5's fix): a user who asks the same LLM
    the same question N times must not get N x 0.4 confidence
    contribution. Exact (llm.lower(), prompt.lower()) dedup via a
    metadata-stored SHA-256 prompt hash.

    HONEST NOTE: this is NOT full semantic deduplication — the same
    question with slightly different wording still ingests a "new"
    analysis. The exact-match hash catches the obvious gaming case
    without penalizing genuine iteration.

    Returns the existing evidence_id if a duplicate is found, else None.
    Never raises (DB failures return None — the ingest proceeds and
    quality-scoring dedup still applies downstream).
    """
    pool = await get_pool()
    if not pool:
        return None

    prompt_hash = _prompt_hash(llm_name, prompt_used)

    try:
        async with pool.acquire() as conn:
            # Look for existing evidence with the same prompt hash
            rows = await conn.fetch(
                """
                SELECT evidence_id FROM evidence
                WHERE case_id = $1
                  AND evidence_type = 'MODEL_INFERENCE'
                  AND source_llm IS NOT NULL
                  AND metadata->>'prompt_hash' = $2
                LIMIT 1
                """,
                case_id, prompt_hash,
            )
            if rows:
                return rows[0]["evidence_id"]
    except Exception as exc:
        logger.debug("Duplicate pre-check failed (migration 015 not applied?): %s", exc)
    return None


async def ingest_external_analysis(
    case_id: str,
    llm_name: str,
    prompt_used: str,
    analysis_text: str,
    conversation_context: Optional[str] = None,
    source_uri: Optional[str] = None,
) -> Evidence:
    """Ingest a piece of external LLM analysis as evidence.

    Creates a HUMAN source (the user brought this in — they are the one who
    interacted with the external LLM and chose to file the analysis),
    evidence of type MODEL_INFERENCE with source_llm set to llm_name, and
    metadata recording the prompt, response, and any conversation context.

    The evidence_type is MODEL_INFERENCE so _evidence_weight can
    distinguish it. The source_llm column lets the weight function credit
    external analyses (0.4) while zeroing out ICE's own inferences (0.0).

    HONEST NOTE (doc Part 2 §12 shortcut #8): the source row is
    source_type='HUMAN' even though the analysis came from an LLM — HUMAN
    is the existing SourceType enum value and adding 'EXTERNAL_LLM' is
    deferred to Part 4. The source_llm column + source metadata
    ("analyst_llm") carry the true origin.

    Args:
        case_id: the case to ingest into.
        llm_name: human label of the LLM, e.g. "claude-3.5-sonnet" or
            "gpt-4o". Stored verbatim; no validation against a model
            registry.
        prompt_used: what the user asked the LLM. Kept verbatim as
            provenance.
        analysis_text: what the LLM answered. Kept verbatim as evidence
            content.
        conversation_context: optional multi-turn context (e.g. prior
            messages or a summary of the back-and-forth).
        source_uri: optional link to the source (e.g. a claude.ai share
            URL).

    Preconditions:
        case_id exists.
        llm_name is non-empty after strip.
        prompt_used is non-empty after strip.
        analysis_text is non-empty after strip.

    Postconditions:
        A sources row exists (source_type='HUMAN', source_uri=source_uri,
        metadata.analyst_llm = llm_name).
        An evidence row exists with:
            evidence_type = 'MODEL_INFERENCE'
            source_llm = llm_name (best effort — see below)
            metadata = {analyst_llm, prompt, response, context, ingested_at,
                        source_uri, prompt_hash}
            content = structured record of prompt + response
        An evidence_ingested event exists in case_events.
        If an identical (llm, prompt) analysis already exists, the EXISTING
        evidence is returned and nothing is inserted (Part 2 §2.7 dedup).

    Raises:
        ValueError on empty required fields.
        CaseNotFoundError if the case does not exist (via get_case).
        DatabaseUnavailableError if pool is None.

    Side effects:
        INSERT INTO sources (1).
        INSERT INTO evidence (1).
        _emit_event("evidence_ingested", {...}) via ingest_evidence.
    """
    if not case_id:
        raise ValueError("case_id must be non-empty")
    if not llm_name or not llm_name.strip():
        raise ValueError("llm_name must be non-empty")
    if not prompt_used or not prompt_used.strip():
        raise ValueError("prompt_used must be non-empty")
    if not analysis_text or not analysis_text.strip():
        raise ValueError("analysis_text must be non-empty")

    # Part 2 §2.7 dedup: check BEFORE creating the source, so a duplicate
    # call leaves no orphaned source row behind.
    existing_id = await _check_external_analysis_duplicate(
        case_id, llm_name, prompt_used
    )
    if existing_id:
        logger.info(
            "External analysis duplicate detected: existing=%s, new prompt for %s",
            existing_id, llm_name,
        )
        return await get_evidence(existing_id)

    # Validate case exists (fail fast before creating source)
    from case_engine.case import get_case
    await get_case(case_id)

    # Source: the user brought this analysis in
    source = await _create_source(
        case_id=case_id,
        source_type="HUMAN",
        source_uri=source_uri,
        metadata={"analyst_llm": llm_name.strip()},
    )

    # Build structured content
    content_parts = [
        f"[External LLM Analysis — {llm_name.strip()}]",
        f"Prompt: {prompt_used.strip()}",
        "",
        "Analysis:",
        analysis_text.strip(),
    ]
    if conversation_context and conversation_context.strip():
        content_parts.extend(["", "Conversation context:", conversation_context.strip()])
    content = "\n".join(content_parts)

    # Ingest as MODEL_INFERENCE
    ev = await ingest_evidence(
        case_id=case_id,
        content=content,
        source_id=source.source_id,
        evidence_type="MODEL_INFERENCE",
        metadata={
            "analyst_llm": llm_name.strip(),
            "prompt": prompt_used.strip(),
            "response": analysis_text.strip(),
            "context": (conversation_context or "").strip(),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "source_uri": source_uri,
            "prompt_hash": _prompt_hash(llm_name, prompt_used),
        },
    )

    # Set source_llm. Use UPDATE so if migration 015 hasn't been applied
    # yet, this fails gracefully (log warning) rather than crashing the
    # ingest. (Honesty shortcut #9's partial mitigation: the warning is at
    # ingest time — an app-startup check for pre-015 MODEL_INFERENCE rows
    # lives in app/main.py, Part 2-b scope.)
    pool = await get_pool()
    if pool:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE evidence SET source_llm = $1 WHERE evidence_id = $2",
                    llm_name.strip(), ev.evidence_id,
                )
        except Exception as exc:
            logger.warning(
                "Could not set source_llm on %s (migration 015 not applied?): %s",
                ev.evidence_id, exc,
            )

    return await get_evidence(ev.evidence_id)


# ---------------------------------------------------------------------------
# §3.16 File ingestion
# ---------------------------------------------------------------------------

async def ingest_evidence_from_file(
    case_id: str,
    file_path: str,
    source_type: SourceType = "DOCUMENT",
    metadata: Optional[dict] = None,
    parent_source_id: Optional[str] = None,
) -> list[Evidence]:
    """Parse a file and ingest as one or more evidence items.

    Preconditions:
        file_path exists and is readable.
        case_id is a valid case.
    Postconditions:
        One Source row created (parent_source_id == None -> lineage root).
        One or more Evidence rows created, one per section.

    Behavior:
        .txt / .md files -> read as single block, no Docling.
        Other files -> Docling DocumentConverter (if DOCLING_ENABLED=true).
        Docling failure -> falls back to reading first 5000 chars as plain text.
        Markdown output is split into sections by ## / ### headers.

    Raises:
        FileNotFoundError: if file_path does not exist.
        DatabaseUnavailableError.
    Side effects:
        INSERT INTO sources (1).
        N calls to ingest_evidence (each emits evidence_ingested).
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    sections: list[str]
    if file_path.endswith((".txt", ".md")):
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            sections = [fh.read()]
    else:
        markdown = ""
        if os.getenv("DOCLING_ENABLED", "true").lower() == "true":
            try:
                from docling.document_converter import DocumentConverter

                converter = DocumentConverter()
                result = converter.convert(file_path)
                markdown = result.document.export_to_markdown()
            except Exception as exc:
                logger.warning(
                    "Docling failed for %s: %s. Falling back to text.", file_path, exc
                )
        if not markdown:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                markdown = fh.read()[:5000]
        sections = _split_markdown_sections(markdown)

    source = await _create_source(
        case_id=case_id,
        source_type=source_type,
        source_uri=file_path,
        metadata=metadata or {"filename": os.path.basename(file_path)},
        parent_source_id=parent_source_id,
    )

    evidence_items: list[Evidence] = []
    for idx, section in enumerate(sections):
        if not section.strip():
            continue
        ev = await ingest_evidence(
            case_id=case_id,
            content=section,
            source_id=source.source_id,
            metadata={**(metadata or {}), "section_index": idx},
        )
        evidence_items.append(ev)
    return evidence_items


def _split_markdown_sections(markdown: str) -> list[str]:
    """Split markdown by ## or ### headers (sync)."""
    parts = re.split(r"\n(?=#{2,3}\s)", markdown)
    return [p.strip() for p in parts if p.strip()]


# ---------------------------------------------------------------------------
# §3.17 URL ingestion
# ---------------------------------------------------------------------------

async def ingest_evidence_from_url(
    case_id: str,
    url: str,
    metadata: Optional[dict] = None,
) -> list[Evidence]:
    """Fetch a URL and ingest as evidence. Uses SCRAPER env var.

    Preconditions: url is a valid http(s) URL.
    Postconditions: one Source row + one or more Evidence rows.
    Raises:
        ValueError: if SCRAPER not in {local, scrapfly, firecrawl}.
    Side effects: network I/O; DB writes via _create_source + ingest_evidence.

    SCRAPER=local (default when no scraper is configured) uses httpx +
    trafilatura via case_engine.web_research: no API key, no JS rendering.
    scrapfly / firecrawl remain available for JS-heavy or bot-protected pages.
    """
    scraper = os.getenv("SCRAPER", "local").lower()
    content = ""

    if scraper == "local":
        from case_engine.web_research import fetch_page_text

        page = await fetch_page_text(url)
        content = page.get("text") or ""
        if page.get("title"):
            metadata = {**(metadata or {}), "title": page["title"]}
    elif scraper == "scrapfly":
        from scrapfly import ScrapflyClient, ScrapeConfig

        client = ScrapflyClient(key=os.getenv("SCRAPFLY_API_KEY", ""))
        result = client.scrape(
            ScrapeConfig(url=url, asp=True, render_js=True, format="markdown")
        )
        content = result.content
    elif scraper == "firecrawl":
        from firecrawl import Firecrawl

        client = Firecrawl(api_key=os.getenv("FIRECRAWL_API_KEY", ""))
        result = client.scrape_url(url, formats=["markdown"])
        content = result.get("markdown", "") if isinstance(result, dict) else ""
    else:
        raise ValueError(f"Unsupported SCRAPER: {scraper}")

    if not content.strip():
        return []

    source = await _create_source(
        case_id=case_id,
        source_type="URL",
        source_uri=url,
        metadata=metadata or {"url": url},
    )
    ev = await ingest_evidence(
        case_id=case_id,
        content=content,
        source_id=source.source_id,
        metadata={**(metadata or {}), "url": url},
    )
    return [ev]

# ===========================================================================
# Hari ICE improvements pass — five-axis evidence credibility assessment.
#
# Second-pass audit: "I would separate these concepts: evidence kind,
# source reliability, observation quality, independence, relevance. These
# are separate axes, not one confidence number." assess_evidence_credibility
# returns exactly those axes, each with its factor breakdown (silent
# scoring is forbidden). quality_score stays what it was (a composite
# convenience heuristic); the axes are the inspectable truth behind it.
# ===========================================================================


async def assess_evidence_credibility(evidence_id: str) -> dict:
    """Assess one evidence item on five SEPARATE credibility axes.

    Axes:
        kind                — the evidence_type (what the content looks like)
        source_reliability  — who produced it: authority × directness of the
                              SOURCE (not the content)
        observation_quality — completeness/reproducibility of the record:
                              timestamps present, direct-observation
                              markers, bounded validity window
        independence        — lineage + duplicate-cluster correlation (are
                              ten copies of one source masquerading as ten
                              confirmations?)
        relevance           — per-hypothesis, computed on demand: token
                              overlap with each hypothesis statement
                              (None when the case has no hypotheses)

    Returns a dict with one entry per axis plus {"factors": {...}}.
    Raises EvidenceNotFoundError / DatabaseUnavailableError.
    """
    ev = await get_evidence(evidence_id)
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot assess credibility: no DB pool")

    async with pool.acquire() as conn:
        source_row = await conn.fetchrow(
            """
            SELECT source_type, authority_score, directness_score,
                   freshness_score, independence_score, lineage_id,
                   duplicate_cluster_id, source_uri
            FROM sources WHERE source_id = $1
            """,
            ev.source_id,
        )
        cluster_siblings = 0
        if ev.duplicate_cluster_id:
            cluster_siblings = await conn.fetchval(
                "SELECT COUNT(*) FROM evidence "
                "WHERE duplicate_cluster_id = $1 AND evidence_id <> $2",
                ev.duplicate_cluster_id, evidence_id,
            ) or 0
        lineage_siblings = 0
        if ev.source_lineage_id:
            lineage_siblings = await conn.fetchval(
                "SELECT COUNT(*) FROM evidence "
                "WHERE source_lineage_id = $1 AND evidence_id <> $2 "
                "AND status = 'ACTIVE'",
                ev.source_lineage_id, evidence_id,
            ) or 0
        hypotheses = await conn.fetch(
            "SELECT hypothesis_id, statement FROM hypotheses "
            "WHERE case_id = $1 AND status NOT IN ('FALSIFIED')",
            ev.case_id,
        )

    # --- axis 1: kind ------------------------------------------------------
    kind = {
        "evidence_type": ev.evidence_type,
        "derived": bool((ev.metadata or {}).get("derivation")),
    }

    # --- axis 2: source reliability ----------------------------------------
    if source_row is None:
        source_reliability = 0.5
        source_factors = {"note": "source row missing"}
    else:
        authority = float(source_row["authority_score"])
        directness = float(source_row["directness_score"])
        freshness = float(source_row["freshness_score"])
        # geometric mean keeps any single 0 from nuking the axis, unlike
        # the old product
        source_reliability = round(
            (authority * directness * freshness) ** (1.0 / 3.0), 3
        )
        source_factors = {
            "source_type": source_row["source_type"],
            "authority": authority,
            "directness": directness,
            "freshness": freshness,
        }

    # --- axis 3: observation quality ---------------------------------------
    content_lower = (ev.content or "").lower()
    direct_markers = sum(
        1 for marker in (
            "observed", "measured", "logged", "recorded", "snapshot",
            "sampled", "reproduced",
        ) if marker in content_lower
    )
    has_temporal = any(
        (ev.observed_at, ev.valid_from, ev.valid_until)
    )
    bounded_window = ev.valid_from is not None and ev.valid_until is not None
    observation_quality = round(
        min(
            1.0,
            0.3
            + 0.2 * (1 if has_temporal else 0)
            + 0.2 * min(1.0, direct_markers / 2.0)
            + 0.15 * (1 if bounded_window else 0)
            + 0.15 * (1 if (ev.metadata or {}) else 0),
        ),
        3,
    )
    observation_factors = {
        "has_temporal_fields": has_temporal,
        "bounded_validity_window": bounded_window,
        "direct_observation_markers": direct_markers,
        "has_metadata": bool(ev.metadata),
    }

    # --- axis 4: independence ----------------------------------------------
    correlated = cluster_siblings > 0
    independence = round(
        max(
            0.0,
            1.0
            - 0.5 * (1 if correlated else 0)
            - 0.25 * min(1.0, lineage_siblings / 4.0),
        ),
        3,
    )
    independence_factors = {
        "duplicate_cluster_siblings": cluster_siblings,
        "same_lineage_active_siblings": lineage_siblings,
        "correlated_cluster": correlated,
        "note": (
            "Ten articles repeating one source are not ten independent "
            "confirmations (second-pass audit) — cluster and lineage "
            "siblings discount this axis."
        ),
    }

    # --- axis 5: relevance (per hypothesis) ---------------------------------
    relevance: dict[str, float] = {}
    ev_tokens = {
        t for t in re.findall(r"[a-z0-9]+", (ev.content or "").lower())
        if len(t) > 2
    }
    for h in hypotheses:
        h_tokens = {
            t for t in re.findall(r"[a-z0-9]+", (h["statement"] or "").lower())
            if len(t) > 2
        }
        if not h_tokens:
            relevance[h["hypothesis_id"]] = 0.0
            continue
        overlap = len(ev_tokens & h_tokens) / len(h_tokens)
        relevance[h["hypothesis_id"]] = round(overlap, 3)

    return {
        "evidence_id": evidence_id,
        "kind": kind,
        "source_reliability": source_reliability,
        "observation_quality": observation_quality,
        "independence": independence,
        "relevance": relevance,
        "factors": {
            "source": source_factors,
            "observation": observation_factors,
            "independence": independence_factors,
            "quality_score_composite": ev.quality_score,
            "note": (
                "Five separate axes, not one confidence number; the stored "
                "quality_score remains a composite convenience heuristic."
            ),
        },
    }
