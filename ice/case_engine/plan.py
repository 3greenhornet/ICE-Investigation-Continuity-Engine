"""case_engine/plan.py — adaptive plan & checklist manager.

Owner:      Hari ICE "addition and improvements" pass. The user's explicit
            late-document requirement: "it should also possess the ability
            to rewrite its plan if the user implemented something better
            ... the original plan should also be there with it just
            crossed not deleted."
Model:
    plan_items    — checklist entries with the full state vocabulary
                    (not_started / in_progress / blocked / completed /
                    completed_unverified / superseded / deferred /
                    rejected), dependencies, evidence links and versions.
    plan_revisions — every plan change is a recorded revision with its
                    reason, evaluation, affected items and WHO decided
                    (user vs ice). Superseded items are NEVER deleted —
                    they stay visible with their replacement pointer.
Three records stay separate (the audit's separation):
    the original specification (objective_ref points at it),
    ICE's evolving plan (these tables),
    the actual implementation (evidence links point at it).
reconcile_plan() compares plan steps against implementation evidence and
classifies each divergence honestly — accidental_divergence /
intentional_evolution / temporary_deviation / unresolved — defaulting to
UNRESOLVED when the evidence cannot tell (never silently reclassifying a
regression as an improvement because the checklist no longer matches).
Deps:       db.connection, case_engine (_emit_event), exceptions.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from db.connection import get_pool
from case_engine import _emit_event
from case_engine.exceptions import DatabaseUnavailableError

logger = logging.getLogger(__name__)

PLAN_STATUSES = (
    "not_started", "in_progress", "blocked", "completed",
    "completed_unverified", "superseded", "deferred", "rejected",
)

_TERMINAL = ("completed", "superseded", "rejected")


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2
    }


def _row_to_item(row) -> dict:
    return {
        "plan_item_id": row["plan_item_id"],
        "case_id": row["case_id"],
        "objective_ref": row["objective_ref"],
        "description": row["description"],
        "status": row["status"],
        "depends_on": list(row["depends_on"] or []),
        "evidence_ids": list(row["evidence_ids"] or []),
        "version": int(row["version"]),
        "supersedes_item": row["supersedes_item"],
        "superseded_by": row["superseded_by"],
        "completion_note": row["completion_note"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def create_plan_item(
    case_id: str,
    description: str,
    objective_ref: Optional[str] = None,
    depends_on: Optional[list[str]] = None,
    supersedes_item: Optional[str] = None,
) -> dict:
    """Add one plan item (or a replacement that supersedes another).

    Postconditions: plan_items row + plan_item_created event. When
    supersedes_item is given, the old item is marked superseded with
    superseded_by pointing at the new item — crossed out, never deleted.
    """
    if not description or not description.strip():
        raise ValueError("description must be non-empty")

    from case_engine.case import get_case

    await get_case(case_id)  # raises CaseNotFoundError

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")

    item_id = f"plan_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO plan_items (
                    plan_item_id, case_id, objective_ref, description,
                    status, depends_on, evidence_ids, version,
                    supersedes_item, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, 'not_started', $5::text[],
                          '{}', 1, $6, $7, $7)
                """,
                item_id, case_id, objective_ref, description.strip(),
                depends_on or [], supersedes_item, now,
            )
            if supersedes_item:
                await conn.execute(
                    """
                    UPDATE plan_items
                    SET status = 'superseded', superseded_by = $1,
                        updated_at = $2
                    WHERE plan_item_id = $3
                    """,
                    item_id, now, supersedes_item,
                )
            await _emit_event(
                case_id, "plan_item_created",
                {
                    "plan_item_id": item_id,
                    "description": description.strip(),
                    "supersedes_item": supersedes_item,
                },
                conn=conn,
            )

    return await get_plan_item(item_id)


async def get_plan_item(plan_item_id: str) -> dict:
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM plan_items WHERE plan_item_id = $1", plan_item_id,
        )
    if row is None:
        raise ValueError(f"Plan item not found: {plan_item_id}")
    return _row_to_item(row)


async def list_plan_items(
    case_id: str, include_superseded: bool = True
) -> list[dict]:
    """List the plan; superseded items remain visible by default."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    status_filter = "" if include_superseded else " AND status <> 'superseded'"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT * FROM plan_items
            WHERE case_id = $1{status_filter}
            ORDER BY created_at
            """,
            case_id,
        )
    return [_row_to_item(r) for r in rows]


async def update_plan_status(
    plan_item_id: str,
    new_status: str,
    note: str = "",
    evidence_ids: Optional[list[str]] = None,
    decided_by: str = "user",
) -> dict:
    """Transition a plan item's status (never deletes; superseded stays).

    Every transition is a plan_revisions row (who decided + why) and a
    plan_item_status_changed event.
    """
    if new_status not in PLAN_STATUSES:
        raise ValueError(f"Invalid plan status: {new_status}")
    current = await get_plan_item(plan_item_id)
    if current["status"] == new_status:
        return current

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    now = datetime.now(timezone.utc)
    revision_id = f"prev_{uuid.uuid4().hex[:12]}"

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE plan_items
                SET status = $1, completion_note = $2, updated_at = $3,
                    version = version + 1,
                    evidence_ids = CASE WHEN $4::text[] IS NULL
                        THEN evidence_ids ELSE $4::text[] END
                WHERE plan_item_id = $5
                """,
                new_status, note, now, evidence_ids, plan_item_id,
            )
            await conn.execute(
                """
                INSERT INTO plan_revisions (
                    revision_id, case_id, from_version, to_version, reason,
                    decided_by, affected_items, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7::text[], $8)
                """,
                revision_id, current["case_id"], current["version"],
                current["version"] + 1,
                note or f"{current['status']} -> {new_status}",
                decided_by, [plan_item_id], now,
            )
            await _emit_event(
                current["case_id"], "plan_item_status_changed",
                {
                    "plan_item_id": plan_item_id,
                    "old_status": current["status"],
                    "new_status": new_status,
                    "note": note[:300],
                    "decided_by": decided_by,
                },
                conn=conn,
            )

    return await get_plan_item(plan_item_id)


async def revise_plan(
    case_id: str,
    reason: str,
    changes: list[dict],
    decided_by: str = "ice",
) -> dict:
    """Apply a batch of plan changes as ONE visible revision.

    Each change: {plan_item_id (optional when superseding), action:
    "add"|"supersede"|"complete"|"defer"|"reject", description?, note?}.
    The revision (reason + evaluation + affected items) is recorded — the
    "why the plan changed" record the user demanded.
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    now = datetime.now(timezone.utc)
    revision_id = f"prev_{uuid.uuid4().hex[:12]}"
    affected: list[str] = []

    async with pool.acquire() as conn:
        async with conn.transaction():
            next_version = await conn.fetchval(
                """
                SELECT COALESCE(MAX(to_version), 0) + 1 FROM plan_revisions
                WHERE case_id = $1
                """,
                case_id,
            ) or 1
            await conn.execute(
                """
                INSERT INTO plan_revisions (
                    revision_id, case_id, from_version, to_version, reason,
                    decided_by, affected_items, created_at
                ) VALUES ($1, $2, NULL, $3, $4, $5, '{}'::text[], $6)
                """,
                revision_id, case_id, next_version, reason, decided_by, now,
            )
    for change in changes:
        action = change.get("action")
        if action == "add":
            item = await create_plan_item(
                case_id=case_id,
                description=change.get("description", ""),
                objective_ref=change.get("objective_ref"),
                depends_on=change.get("depends_on"),
            )
            affected.append(item["plan_item_id"])
        elif action == "supersede":
            old_id = change.get("plan_item_id")
            item = await create_plan_item(
                case_id=case_id,
                description=change.get("description", ""),
                supersedes_item=old_id,
            )
            affected.append(item["plan_item_id"])
        elif action in ("complete", "defer", "reject"):
            status = {"complete": "completed", "defer": "deferred",
                      "reject": "rejected"}[action]
            item = await update_plan_status(
                change.get("plan_item_id"), status,
                note=change.get("note", reason), decided_by=decided_by,
            )
            affected.append(item["plan_item_id"])

    if affected:
        pool2 = await get_pool()
        async with pool2.acquire() as conn:
            await conn.execute(
                "UPDATE plan_revisions SET affected_items = $1::text[] "
                "WHERE revision_id = $2",
                affected, revision_id,
            )
            await _emit_event(
                case_id, "plan_revised",
                {
                    "revision_id": revision_id,
                    "reason": reason[:300],
                    "affected_items": affected,
                    "decided_by": decided_by,
                },
                conn=conn,
            )

    return {
        "revision_id": revision_id,
        "reason": reason,
        "affected_items": affected,
        "decided_by": decided_by,
    }


async def list_plan_revisions(case_id: str) -> list[dict]:
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM plan_revisions WHERE case_id = $1 "
            "ORDER BY created_at DESC", case_id,
        )
    return [
        {
            "revision_id": r["revision_id"],
            "from_version": r["from_version"],
            "to_version": r["to_version"],
            "reason": r["reason"],
            "evaluation": r["evaluation"],
            "decided_by": r["decided_by"],
            "affected_items": list(r["affected_items"] or []),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Reconciliation: plan vs implementation evidence
# ---------------------------------------------------------------------------


async def reconcile_plan(case_id: str) -> dict:
    """Compare open plan items against implementation evidence.

    For every not_started / in_progress item, look for evidence (claims +
    evidence content) indicating the objective is met — by ANY
    implementation, not just the planned one. Classification is honest:
    'unresolved' unless the evidence clearly says otherwise; a keyword
    match alone NEVER classifies intentional_evolution (that requires a
    recorded user decision — a plan_revisions row with decided_by='user').

    Returns {per_item: [...], summary: {...}} — a PROPOSAL, not a mutation:
    the caller (or the user) turns it into revise_plan() calls.
    """
    from case_engine.claim import get_claims_for_case

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")

    items = [
        i for i in await list_plan_items(case_id)
        if i["status"] in ("not_started", "in_progress", "blocked")
    ]
    claims = [c.statement for c in await get_claims_for_case(case_id)]
    async with pool.acquire() as conn:
        ev_rows = await conn.fetch(
            "SELECT content FROM evidence WHERE case_id = $1 "
            "AND status = 'ACTIVE'", case_id,
        )
    evidence_texts = [r["content"] for r in ev_rows]
    corpus = claims + evidence_texts

    # recorded user decisions (intentional evolution requires one)
    user_revisions = [
        r for r in await list_plan_revisions(case_id) if r["decided_by"] == "user"
    ]

    per_item = []
    for item in items:
        item_tokens = _tokens(item["description"])
        best_cov, best_source = 0.0, None
        for text in corpus:
            cov = len(item_tokens & _tokens(text)) / max(1, len(item_tokens))
            if cov > best_cov:
                best_cov, best_source = cov, text

        objective_met = best_cov >= 0.6
        has_user_decision = any(
            item["plan_item_id"] in (r["affected_items"] or [])
            for r in user_revisions
        )

        if objective_met and has_user_decision:
            classification = "intentional_evolution"
        elif objective_met:
            classification = "completed_unverified_candidate"
        elif 0.3 <= best_cov < 0.6:
            classification = "unresolved"
        else:
            classification = "not_evidenced"

        per_item.append(
            {
                "plan_item_id": item["plan_item_id"],
                "description": item["description"],
                "status": item["status"],
                "evidence_coverage": round(best_cov, 3),
                "best_evidence_excerpt": (best_source or "")[:200],
                "classification": classification,
                "proposed_action": (
                    "mark completed_unverified" if classification ==
                    "completed_unverified_candidate" else
                    "record user decision, then supersede" if
                    objective_met else
                    "investigate or keep open"
                ),
            }
        )

    return {
        "case_id": case_id,
        "per_item": per_item,
        "summary": {
            "open_items": len(per_item),
            "objective_met_candidates": sum(
                1 for p in per_item
                if p["classification"] in
                ("completed_unverified_candidate", "intentional_evolution")
            ),
            "unresolved": sum(
                1 for p in per_item if p["classification"] == "unresolved"
            ),
            "note": (
                "A proposal, not a mutation: keyword evidence alone never "
                "reclassifies a plan step — intentional evolution requires "
                "a recorded user decision."
            ),
        },
    }
