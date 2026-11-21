"""case_engine/consistency.py — case coherence validation (improvisation C).

Owner:      ICE revamp, reviewer audit improvisation C.
Purpose:    validate_case(case_id) answers "is this case in a coherent
            state?" Run it before decide() or from the CLI/API to catch
            referential corruption during development.
Deps:       db.connection only — pure read-only SQL checks, no
            case_engine-internal imports (keeps this usable from anywhere in
            the dependency graph).

Checks (per the audit list, reduced to the five the implementation task
specifies — action-target checks are explicitly out of scope):
    1. Hypotheses referencing a missing current_version_id.
    2. Claims whose supporting_evidence_ids reference missing evidence.
    3. Contradictions referencing missing claims.
    4. Gaps discriminating missing hypotheses.
    5. case_events.event_seq monotonic with no gaps (seq == index + 1).

Contract: returns a list of human-readable violation strings; an empty list
means the case is coherent. NEVER raises — returns ["db_unavailable"] when
the pool is None and ["db_error: ..."] if a query fails.
"""

import logging

from db.connection import get_pool

logger = logging.getLogger(__name__)


async def validate_case(case_id: str) -> list[str]:
    """Validate the referential + event-log coherence of a case.

    Pure read-only. Returns [] when coherent; otherwise one violation string
    per problem found. Never raises (returns ["db_unavailable"] if no pool).
    """
    violations: list[str] = []

    pool = await get_pool()
    if pool is None:
        return ["db_unavailable"]

    try:
        # 1 — hypotheses with a dangling current_version_id.
        rows = await _fetch(
            pool,
            """
            SELECT h.hypothesis_id, h.current_version_id
            FROM hypotheses h
            LEFT JOIN hypothesis_versions v ON v.version_id = h.current_version_id
            WHERE h.case_id = $1
              AND h.current_version_id IS NOT NULL
              AND v.version_id IS NULL
            """,
            case_id,
        )
        for row in rows:
            violations.append(
                f"hypothesis {row['hypothesis_id']} references missing "
                f"current_version_id {row['current_version_id']}"
            )

        # 2 — claims referencing missing evidence (supporting ids).
        rows = await _fetch(
            pool,
            """
            SELECT c.claim_id, u.eid
            FROM claims c
            CROSS JOIN LATERAL unnest(COALESCE(c.supporting_evidence_ids, '{}'::text[])) AS u(eid)
            LEFT JOIN evidence e ON e.evidence_id = u.eid
            WHERE c.case_id = $1 AND e.evidence_id IS NULL
            """,
            case_id,
        )
        for row in rows:
            violations.append(
                f"claim {row['claim_id']} references missing evidence {row['eid']}"
            )

        # 3 — contradictions referencing missing claims.
        rows = await _fetch(
            pool,
            """
            SELECT ct.contradiction_id, ct.claim_a, ct.claim_b
            FROM contradictions ct
            LEFT JOIN claims ca ON ca.claim_id = ct.claim_a
            LEFT JOIN claims cb ON cb.claim_id = ct.claim_b
            WHERE ct.case_id = $1 AND (ca.claim_id IS NULL OR cb.claim_id IS NULL)
            """,
            case_id,
        )
        for row in rows:
            violations.append(
                f"contradiction {row['contradiction_id']} references missing "
                f"claim(s) {row['claim_a']} / {row['claim_b']}"
            )

        # 4 — gaps discriminating missing hypotheses.
        rows = await _fetch(
            pool,
            """
            SELECT g.gap_id, u.hid
            FROM information_gaps g
            CROSS JOIN LATERAL unnest(COALESCE(g.discriminating_hypothesis_ids, '{}'::text[])) AS u(hid)
            LEFT JOIN hypotheses h ON h.hypothesis_id = u.hid
            WHERE g.case_id = $1 AND h.hypothesis_id IS NULL
            """,
            case_id,
        )
        for row in rows:
            violations.append(
                f"gap {row['gap_id']} discriminates missing hypothesis {row['hid']}"
            )

        # 5 — event_seq monotonic with no gaps (seq == index + 1).
        rows = await _fetch(
            pool,
            "SELECT event_seq FROM case_events WHERE case_id = $1 ORDER BY event_seq ASC",
            case_id,
        )
        for idx, row in enumerate(rows):
            expected = idx + 1
            if row["event_seq"] != expected:
                violations.append(
                    f"case_events event_seq gap: position {idx} expected "
                    f"{expected}, got {row['event_seq']}"
                )
    except Exception as exc:  # never raise out of a validator
        logger.warning("validate_case(%s) hit a database error: %s", case_id, exc)
        return [f"db_error: {exc}"]

    return violations


async def _fetch(pool, sql: str, *args):
    """Tiny helper: acquire, fetch, release."""
    async with pool.acquire() as conn:
        return await conn.fetch(sql, *args)