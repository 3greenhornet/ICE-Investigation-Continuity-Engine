"""case_engine/contradiction.py — contradiction detection between claims.

Owner: investigation logic layer (Part 3, section 1).
Purpose: detect REAL contradictions between claims. False positives are
rejected via scope and temporal pre-checks before any LLM call is made.
Persists contradictions, exposes query/resolve operations, and emits the
``contradiction_detected`` / ``contradiction_resolved`` events.

Downstream dependencies (allowed):
    - db.connection.get_pool
    - case_engine._emit_event
    - case_engine.exceptions (typed errors)
    - case_engine.llm._call_llm_json
    - case_engine.schemas (Claim, Contradiction, RelationType)
    - case_engine.claim (public: get_claim, get_claims_for_case)
    - case_engine.evidence (public: get_evidence)
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

from db.connection import get_pool
from case_engine import _emit_event
from case_engine.exceptions import (
    CaseEngineError,
    ClaimNotFoundError,
    ContradictionNotFoundError,
    DatabaseUnavailableError,
)
from case_engine.llm import _call_llm_json
from case_engine.schemas import Claim, Contradiction, RelationType
from case_engine.claim import get_claim, get_claims_for_case
from case_engine.evidence import get_evidence

logger = logging.getLogger(__name__)

IMPLICATION_PROMPT = '''Determine the logical relationship between these two claims.

CLAIM A: {statement_a}
CLAIM B: {statement_b}

Classify the relationship as exactly one of:
- "contradiction": A and B cannot both be true
- "refinement": A is a more specific version of B
- "scope_mismatch": A and B apply to different contexts
- "temporal_mismatch": A and B apply to different time periods
- "independent": A and B are unrelated

Also provide a confidence score from 0.0 to 1.0.

Return ONLY a JSON object:
{{"relation_type": "...", "confidence": 0.0, "reasoning": "..."}}
'''


def _row_to_contradiction(row) -> Contradiction:
    """Map an asyncpg Record from ``contradictions`` to a Contradiction model."""
    # P3/I3 (merge spec Part 1 §5.3): jsonb columns can come back as raw
    # JSON strings (custom codec / JSONB->TEXT migration); parse defensively.
    scope_a = row["scope_a"]
    if isinstance(scope_a, str):
        try:
            scope_a = json.loads(scope_a)
        except Exception:
            scope_a = None
    scope_b = row["scope_b"]
    if isinstance(scope_b, str):
        try:
            scope_b = json.loads(scope_b)
        except Exception:
            scope_b = None

    return Contradiction(
        contradiction_id=row["contradiction_id"],
        case_id=row["case_id"],
        claim_a=row["claim_a"],
        claim_b=row["claim_b"],
        scope_a=scope_a,
        scope_b=scope_b,
        time_a=row["time_a"],
        time_b=row["time_b"],
        relation_type=row["relation_type"],
        severity=row["severity"],
        source_evidence_a=row["source_evidence_a"],
        source_evidence_b=row["source_evidence_b"],
        status=row["status"],
        resolution_summary=row["resolution_summary"],
        resolution_evidence_id=row["resolution_evidence_id"],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
    )


async def check_scope_compatibility(
    claim_a: Claim,
    claim_b: Claim,
) -> Tuple[bool, Optional[str]]:
    """Determine whether two claims' scopes are compatible.

    Preconditions:
        Both claims are Claim instances.
    Postconditions:
        Returns (compatible, reason).
        compatible=True means "could both be true because they might apply
        to overlapping contexts".
        compatible=False means "they apply to provably disjoint contexts --
        the caller should NOT run contradiction detection".
        reason is None when compatible=True, else a short string.
    Raises: nothing.
    Side effects: none.

    Rules (evaluated in order):
        1. If both scopes are None or empty -> compatible=True.
        2. For every key present in BOTH scopes:
           a. If both values are equal -> compatible.
           b. If both values are single scalars and differ -> compatible=False,
              reason="Different <key>: <a> vs <b>".
           c. If either value is a list -> check for any shared element.
              If no shared element -> compatible=False,
              reason="Disjoint <key>: <a> vs <b>".
              If shared element exists -> continue.
        3. If no rule disqualifies -> compatible=True.
    """
    scope_a = claim_a.scope or {}
    scope_b = claim_b.scope or {}

    if not scope_a or not scope_b:
        return True, None

    for key in set(scope_a.keys()) & set(scope_b.keys()):
        val_a = scope_a[key]
        val_b = scope_b[key]

        if val_a == val_b:
            continue

        if isinstance(val_a, list) or isinstance(val_b, list):
            list_a = val_a if isinstance(val_a, list) else [val_a]
            list_b = val_b if isinstance(val_b, list) else [val_b]
            if not set(list_a) & set(list_b):
                return False, f"Disjoint {key}: {list_a} vs {list_b}"
            continue

        return False, f"Different {key}: {val_a} vs {val_b}"

    return True, None


async def check_temporal_compatibility(
    claim_a: Claim,
    claim_b: Claim,
) -> Tuple[bool, Optional[str]]:
    """Determine whether two claims' time ranges are compatible.

    Preconditions: both claims are Claim instances.
    Postconditions:
        Returns (compatible, reason).
        compatible=True means "the time ranges overlap or are unbounded".
        compatible=False means "the time ranges are provably disjoint".
    Raises: nothing.
    Side effects: none.

    Rules:
        - If either claim has BOTH valid_from and valid_until None ->
          compatible=True (unbounded).
        - Else, treat missing bounds as -inf / +inf.
        - Compute [a_start, a_end] and [b_start, b_end].
        - If a_end < b_start OR b_end < a_start -> compatible=False,
          reason="Non-overlapping time ranges: [a_start, a_end] vs
          [b_start, b_end]".
        - Else compatible=True.
    """
    a_start = claim_a.valid_from
    a_end = claim_a.valid_until
    b_start = claim_b.valid_from
    b_end = claim_b.valid_until

    if a_start is None and a_end is None:
        return True, None
    if b_start is None and b_end is None:
        return True, None

    _min = datetime.min.replace(tzinfo=timezone.utc)
    _max = datetime.max.replace(tzinfo=timezone.utc)

    a_start = a_start or _min
    a_end = a_end or _max
    b_start = b_start or _min
    b_end = b_end or _max

    if a_end < b_start or b_end < a_start:
        return False, (
            f"Non-overlapping time ranges: [{a_start.isoformat()}, {a_end.isoformat()}] "
            f"vs [{b_start.isoformat()}, {b_end.isoformat()}]"
        )
    return True, None


async def llm_implication_check(
    claim_a: Claim,
    claim_b: Claim,
) -> Tuple[RelationType, float]:
    """Ask the LLM to classify the relationship between two claims.

    Preconditions: both claims have non-empty statements.
    Postconditions:
        Returns (relation_type, confidence).
        relation_type is one of the five RelationType literals.
        confidence is in [0.0, 1.0].
        If the LLM returns invalid data, returns ("independent", 0.0).
    Raises: nothing (defensive against LLM output).
    Side effects: one LLM call.
    """
    valid_relations = {
        "contradiction", "refinement", "scope_mismatch",
        "temporal_mismatch", "independent",
    }

    # Hari ICE improvements pass: prompt centralisation (id
    # "contradiction_classification", text preserved verbatim; ledger records
    # prompt id/version, RANKER role chain when configured).
    from case_engine.prompts import render as render_prompt
    _pid, _pver, prompt = render_prompt(
        "contradiction_classification",
        statement_a=claim_a.statement,
        statement_b=claim_b.statement,
    )
    raw = await _call_llm_json(
        prompt,
        temperature=0.1,
        max_retries=3,
        role="RANKER",
        operation="contradiction_classification",
        prompt_id=_pid,
        prompt_version=_pver,
        case_id=claim_a.case_id,
    )

    if not raw or not isinstance(raw, dict):
        return "independent", 0.0

    relation = raw.get("relation_type", "independent")
    if relation not in valid_relations:
        return "independent", 0.0

    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return relation, confidence  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Hari ICE improvements pass — deterministic implication fallback.
#
# llm_implication_check is LLM-only, which made contradiction detection
# silently impossible offline (the rebuilt benchmark recorded 0 detected
# pairs purely for lack of an API key). This fallback implements
# antonym-cue contradiction detection: two claims about the SAME subject
# carrying OPPOSITE value cues ("dropped" vs "unchanged", "regressed" vs
# "stable", ...) are classified as a contradiction with modest confidence.
# It is deliberately conservative (subject overlap required, cue pairs
# curated) and clearly marked via the reasoning string.
# ---------------------------------------------------------------------------

_ANTONYM_CUES: list[tuple[frozenset[str], frozenset[str]]] = [
    (frozenset({"dropped", "fell", "decreased", "declined"}),
     frozenset({"unchanged", "stable", "flat", "remained", "stayed"})),
    (frozenset({"increased", "rose", "spiked", "grew", "regressed"}),
     frozenset({"decreased", "fell", "dropped", "improved", "normal"})),
    (frozenset({"healthy", "operational", "passing", "available"}),
     frozenset({"unavailable", "down", "failing", "errors", "outage"})),
    (frozenset({"slow", "slower", "degraded"}),
     frozenset({"fast", "faster", "improved", "normal"})),
    (frozenset({"before"}),
     frozenset({"after"})),
    (frozenset({"present", "exists", "enabled"}),
     frozenset({"absent", "missing", "removed", "disabled"})),
]

_SALIENT_STOP = frozenset(
    "the a an and or of to in on for with was were is are be been it its "
    "this that these those not no we i you they at by from as after before "
    "during since about into over under then than so very just also".split()
)


def _deterministic_implication_check(
    claim_a, claim_b
) -> tuple[str, float]:
    """Antonym-cue contradiction detection (see block comment above).

    Returns (relation_type, confidence): ("contradiction", ~0.75) on a
    same-subject opposite-cue pair, ("independent", 0.0) otherwise.
    """
    import re as _re

    def toks(c) -> set[str]:
        return {
            t for t in _re.findall(r"[a-z0-9]+", (c.statement or "").lower())
            if len(t) > 2 and t not in _SALIENT_STOP
        }

    ta, tb = toks(claim_a), toks(claim_b)
    shared_subject = ta & tb
    if not shared_subject:
        return "independent", 0.0

    sa = " ".join(ta)
    sb = " ".join(tb)
    for cue_a_set, cue_b_set in _ANTONYM_CUES:
        a_has = any(c in sa for c in cue_a_set)
        b_has = any(c in sb for c in cue_b_set)
        b_has_rev = any(c in sb for c in cue_a_set)
        a_has_rev = any(c in sa for c in cue_b_set)
        if (a_has and b_has) or (a_has_rev and b_has_rev):
            shared_str = " ".join(sorted(shared_subject)[:3])
            logger.debug(
                "deterministic contradiction: cues %s/%s over subject '%s'",
                cue_a_set, cue_b_set, shared_str,
            )
            return "contradiction", 0.75
    return "independent", 0.0


async def detect_contradiction(
    claim_a_id: str,
    claim_b_id: str,
    confidence_threshold: float = 0.7,
) -> Optional[Contradiction]:
    """Run the full pipeline. Returns None if not a real contradiction.

    Idempotency (Part 2 §9, fix 2H — Part 1's B.2 gap: running the cycle
    twice used to create duplicate contradiction rows):
        1. Pre-check: an existing contradiction on (case_id, claim_a,
           claim_b) in EITHER order returns the existing row immediately
           (no LLM call, no event).
        2. The INSERT uses ON CONFLICT (case_id, claim_pair_key) DO
           NOTHING RETURNING * — claim_pair_key normalizes (A,B) and (B,A)
           to one key (migration 016), so the database enforces uniqueness
           atomically even under a concurrent-detection race. On conflict
           the existing row is re-fetched and returned WITHOUT emitting a
           second contradiction_detected event (exactly one event per
           actual mutation).

        PREREQUISITE: migration 016 (claim_pair_key column + unique
        constraints) must be applied — the ON CONFLICT clause references
        its generated column. Run scripts/dedupe_contradictions.sql before
        016 on databases with pre-existing duplicates.

    Preconditions:
        Both claim IDs exist and belong to the same case.
    Postconditions:
        On contradiction (new):
            A contradictions row is created.
            Returns the Contradiction.
            contradiction_detected event emitted.
        On contradiction (already existing, either order):
            Returns the existing Contradiction. No new row, no event.
        On non-contradiction:
            Returns None. No row created. No event emitted.
    Raises:
        ValueError if the two claims belong to different cases.
        ClaimNotFoundError if either claim is missing.
        DatabaseUnavailableError.
        RuntimeError if the INSERT conflicted yet no existing row can be
            found (inconsistent state).
    Side effects (on NEW contradiction only):
        INSERT INTO contradictions (1).
        _emit_event("contradiction_detected", {...}).
    """
    # ICE flag (spec §9 / §19.4; benchmark baseline B4 toggles it). Read at
    # CALL time, not import time — audit gap #39: baselines and tests set
    # os.environ after case_engine is already imported. When disabled, this
    # is a stub: no claim loads, no LLM calls, returns None.
    if os.getenv("ENABLE_CONTRADICTIONS", "true").lower() != "true":
        return None

    claim_a = await get_claim(claim_a_id)
    claim_b = await get_claim(claim_b_id)

    # Self-pair guard: a claim cannot contradict itself. The case-mode hook
    # (engine/generate.py) already skips same-claim pairs at the call site,
    # but this is a public API — return None rather than creating a
    # degenerate (A, A) row whose claim_pair_key would collide with itself.
    if claim_a_id == claim_b_id:
        return None

    if claim_a.case_id != claim_b.case_id:
        raise ValueError(
            f"Cannot compare claims from different cases: {claim_a.case_id} vs {claim_b.case_id}"
        )

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot detect contradiction: no DB pool")

    # Part 2 §9.2: pre-check for an existing contradiction on this pair in
    # either order — cheap, avoids a wasted LLM call, and keeps re-running
    # the cycle idempotent.
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            """
            SELECT contradiction_id FROM contradictions
            WHERE case_id = $1
              AND ((claim_a = $2 AND claim_b = $3) OR (claim_a = $3 AND claim_b = $2))
            """,
            claim_a.case_id, claim_a_id, claim_b_id,
        )
    if existing:
        logger.debug(
            "Contradiction already exists for (%s, %s): %s",
            claim_a_id, claim_b_id, existing["contradiction_id"],
        )
        return await _load_contradiction(existing["contradiction_id"])

    scope_ok, scope_reason = await check_scope_compatibility(claim_a, claim_b)
    if not scope_ok:
        logger.debug("Scope mismatch: %s", scope_reason)
        return None

    temporal_ok, temporal_reason = await check_temporal_compatibility(claim_a, claim_b)
    if not temporal_ok:
        logger.debug("Temporal mismatch: %s", temporal_reason)
        return None

    # Hari ICE improvements pass: offline fallback — when no LLM provider
    # is configured the deterministic antonym-cue check runs instead of
    # raising (same pattern as claim extraction's fallback).
    from case_engine.exceptions import LLMUnavailableError

    try:
        relation, confidence = await llm_implication_check(claim_a, claim_b)
    except LLMUnavailableError:
        relation, confidence = _deterministic_implication_check(claim_a, claim_b)

    if relation != "contradiction" or confidence < confidence_threshold:
        logger.debug("Not a contradiction: %s (%.2f)", relation, confidence)
        return None

    contradiction_id = f"contra_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    # Part 2 §9.3: INSERT ... ON CONFLICT DO NOTHING RETURNING * handles the
    # concurrent-race case the pre-check cannot (two workers both passing
    # the check, both INSERTing). claim_pair_key is migration 016's
    # LEAST/GREATEST-normalized generated column.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO contradictions (
                contradiction_id, case_id, claim_a, claim_b,
                scope_a, scope_b, time_a, time_b,
                relation_type, severity, status, created_at
            ) VALUES (
                $1, $2, $3, $4,
                $5::jsonb, $6::jsonb, $7, $8,
                $9, $10, 'active', $11
            )
            ON CONFLICT (case_id, claim_pair_key) DO NOTHING
            RETURNING *
            """,
            contradiction_id, claim_a.case_id, claim_a_id, claim_b_id,
            json.dumps(claim_a.scope) if claim_a.scope else None,
            json.dumps(claim_b.scope) if claim_b.scope else None,
            claim_a.valid_from, claim_b.valid_from,
            relation, confidence, now,
        )

    if row is None:
        # Race condition: another worker inserted first. Load the existing
        # one — and emit NO event (exactly one contradiction_detected per
        # real mutation; the winner already emitted it).
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM contradictions
                WHERE case_id = $1
                  AND ((claim_a = $2 AND claim_b = $3) OR (claim_a = $3 AND claim_b = $2))
                """,
                claim_a.case_id, claim_a_id, claim_b_id,
            )
        if row is None:
            raise RuntimeError(
                "Contradiction INSERT returned nothing and pre-check found "
                "nothing — inconsistent state"
            )
        return _row_to_contradiction(row)

    # Normal path — exactly one event per actual mutation.
    await _emit_event(claim_a.case_id, "contradiction_detected", {
        "contradiction_id": contradiction_id,
        "claim_a": claim_a_id,
        "claim_b": claim_b_id,
        "confidence": confidence,
    })

    return _row_to_contradiction(row)


async def _load_contradiction(contradiction_id: str) -> Contradiction:
    """Load a contradiction row by ID. Raises ContradictionNotFoundError."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot load contradiction: no DB pool")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM contradictions WHERE contradiction_id = $1", contradiction_id
        )
    if row is None:
        raise ContradictionNotFoundError(f"Contradiction not found: {contradiction_id}")
    return _row_to_contradiction(row)


async def get_contradiction(contradiction_id: str) -> Contradiction:
    """Load a contradiction by ID (public loader).

    GAP FIX (reviewer audit #43): the API needs GET /contradictions/{id};
    Part 3 section 1.8 only defined the private ``_load_contradiction``.
    Behavior is identical to the private loader; this is the public face
    for the route layer.

    Raises:
        ContradictionNotFoundError if the ID is unknown.
        DatabaseUnavailableError.
    """
    return await _load_contradiction(contradiction_id)


async def find_contradictions_for_claim(claim_id: str) -> list[Contradiction]:
    """Return all contradictions (any status) involving this claim, either as A or B."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot query contradictions: no DB pool")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM contradictions
            WHERE claim_a = $1 OR claim_b = $1
            ORDER BY created_at DESC
            """,
            claim_id,
        )
    return [_row_to_contradiction(row) for row in rows]


async def get_active_contradictions(case_id: str) -> list[Contradiction]:
    """Return all ACTIVE contradictions for a case, ordered by severity DESC."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot query contradictions: no DB pool")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM contradictions
            WHERE case_id = $1 AND status = 'active'
            ORDER BY severity DESC, created_at DESC
            """,
            case_id,
        )
    return [_row_to_contradiction(row) for row in rows]


async def resolve_contradiction(
    contradiction_id: str,
    resolution_evidence_id: str,
    summary: str,
) -> Contradiction:
    """Mark a contradiction resolved by evidence.

    Preconditions:
        contradiction_id exists.
        resolution_evidence_id exists and belongs to the same case.
        summary is non-empty.
    Postconditions:
        contradictions.status == 'resolved'.
        contradictions.resolution_summary and resolution_evidence_id set.
        contradictions.resolved_at == now.
        contradiction_resolved event emitted.
    Raises:
        ContradictionNotFoundError, EvidenceNotFoundError.
        ValueError on empty summary or cross-case evidence.
        DatabaseUnavailableError.
    Side effects:
        UPDATE contradictions (1).
        _emit_event("contradiction_resolved", {...}).
    """
    if not summary or not summary.strip():
        raise ValueError("summary must be non-empty")

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot resolve: no DB pool")

    async with pool.acquire() as conn:
        contradiction_row = await conn.fetchrow(
            "SELECT * FROM contradictions WHERE contradiction_id = $1", contradiction_id
        )
    if contradiction_row is None:
        raise ContradictionNotFoundError(f"Contradiction not found: {contradiction_id}")

    evidence = await get_evidence(resolution_evidence_id)
    if evidence.case_id != contradiction_row["case_id"]:
        raise ValueError("Resolution evidence belongs to a different case")

    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE contradictions
            SET status = 'resolved',
                resolution_summary = $1,
                resolution_evidence_id = $2,
                resolved_at = $3
            WHERE contradiction_id = $4
            """,
            summary.strip(), resolution_evidence_id, now, contradiction_id,
        )

    await _emit_event(contradiction_row["case_id"], "contradiction_resolved", {
        "contradiction_id": contradiction_id,
        "resolution_evidence_id": resolution_evidence_id,
        "summary": summary.strip(),
    })

    return await _load_contradiction(contradiction_id)