"""case_engine/introspection.py — process-mining-lite over case_events.

Owner:      Hari ICE "addition and improvements" pass. The PM4Py inversion:
            "use a business-process analytics tool to investigate the
            development and reasoning behaviour of the AI that runs it."
            This module implements the directly-follows-graph core of
            process mining in pure SQL + Python (no PM4Py dependency — the
            mechanism, not the library), answering the audit's questions:
              * Which actions are repeatedly proposed but rarely executed?
              * Where does the cycle repeatedly return to the same stage?
              * How often does an investigation end without resolving its
                principal uncertainty?
              * Are there differences between research and debug cases?
Read-only: no events, no mutations.
Deps:       db.connection, case_engine.exceptions.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Optional

from db.connection import get_pool
from case_engine.exceptions import DatabaseUnavailableError

logger = logging.getLogger(__name__)


async def build_directly_follows_graph(
    case_id: Optional[str] = None,
) -> dict:
    """Directly-follows graph: {(from_type, to_type): count} + node counts.

    The DFG is the core structure process mining discovers from an event
    log; ours is computed over consecutive case_events pairs per case
    (event_seq order), optionally restricted to one case.
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    async with pool.acquire() as conn:
        if case_id:
            rows = await conn.fetch(
                """
                SELECT event_type, event_seq, case_id FROM case_events
                WHERE case_id = $1 ORDER BY event_seq
                """,
                case_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT event_type, event_seq, case_id FROM case_events "
                "ORDER BY case_id, event_seq"
            )

    edges: Counter = Counter()
    nodes: Counter = Counter()
    prev_type = None
    prev_case = None
    for r in rows:
        etype = r["event_type"]
        nodes[etype] += 1
        if prev_type is not None and r["case_id"] == prev_case:
            edges[(prev_type, etype)] += 1
        prev_type, prev_case = etype, r["case_id"]

    return {
        "nodes": dict(nodes.most_common()),
        "edges": [
            {"from": a, "to": b, "count": n}
            for (a, b), n in edges.most_common()
        ],
        "event_count": len(rows),
    }


async def investigation_flow_report(case_id: str) -> dict:
    """Per-case flow diagnostics (the audit's stall questions)."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")

    async with pool.acquire() as conn:
        proposed = await conn.fetchval(
            "SELECT COUNT(*) FROM actions WHERE case_id = $1", case_id,
        ) or 0
        executed = await conn.fetchval(
            "SELECT COUNT(*) FROM actions WHERE case_id = $1 "
            "AND status = 'completed'", case_id,
        ) or 0
        failed = await conn.fetchval(
            "SELECT COUNT(*) FROM actions WHERE case_id = $1 "
            "AND status = 'failed'", case_id,
        ) or 0
        uninformative = await conn.fetchval(
            "SELECT COUNT(*) FROM actions WHERE case_id = $1 "
            "AND outcome IN ('UNINFORMATIVE', 'UNSUPPORTED')", case_id,
        ) or 0
        proposed_by_type = await conn.fetch(
            """
            SELECT action_type, COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE status = 'completed') AS done,
                   COUNT(*) FILTER (WHERE status = 'failed') AS failed
            FROM actions WHERE case_id = $1 GROUP BY action_type
            """,
            case_id,
        )
        case_row = await conn.fetchrow(
            "SELECT status, case_type, required_confidence FROM cases "
            "WHERE case_id = $1", case_id,
        )
        unresolved_gaps = await conn.fetchval(
            "SELECT COUNT(*) FROM information_gaps "
            "WHERE case_id = $1 AND resolved_at IS NULL", case_id,
        ) or 0
        active_contradictions = await conn.fetchval(
            "SELECT COUNT(*) FROM contradictions "
            "WHERE case_id = $1 AND status = 'active'", case_id,
        ) or 0

    dfg = await build_directly_follows_graph(case_id)

    # loop detection: self-edges and short A->B->A cycles in the DFG
    loops = [
        {"loop": [e["from"], e["to"], e["from"]], "strength": e["count"]}
        for e in dfg["edges"][:40]
        for e2 in dfg["edges"][:40]
        if e["to"] == e2["from"] and e2["to"] == e["from"]
        and e["from"] < e["to"]  # dedupe symmetric pairs
    ][:10]

    status = dict(case_row) if case_row else {}
    unresolved_principal = bool(
        status.get("status") in ("resolved", "abandoned")
        and (unresolved_gaps > 0 or active_contradictions > 0)
    )

    return {
        "case_id": case_id,
        "case_status": status.get("status"),
        "case_type": status.get("case_type"),
        "actions": {
            "proposed": proposed,
            "executed": executed,
            "failed": failed,
            "uninformative_or_unsupported": uninformative,
            "execution_rate": round(executed / proposed, 3) if proposed else None,
            "by_type": [
                {
                    "action_type": r["action_type"],
                    "proposed": int(r["n"]),
                    "completed": int(r["done"]),
                    "failed": int(r["failed"]),
                    "never_executed": int(r["n"]) - int(r["done"]) - int(r["failed"]),
                }
                for r in proposed_by_type
            ],
        },
        "loops": loops,
        "resolution": {
            "unresolved_gaps": unresolved_gaps,
            "active_contradictions": active_contradictions,
            "ended_with_unresolved_uncertainty": unresolved_principal,
        },
        "directly_follows_graph": dfg,
    }


async def fleet_flow_report() -> dict:
    """Cross-case comparison (debug vs research vs explore stall patterns)."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.case_id, c.case_type, c.status,
                   (SELECT COUNT(*) FROM actions a
                    WHERE a.case_id = c.case_id) AS proposed,
                   (SELECT COUNT(*) FROM actions a
                    WHERE a.case_id = c.case_id
                      AND a.status = 'completed') AS executed,
                   (SELECT COUNT(*) FROM actions a
                    WHERE a.case_id = c.case_id
                      AND a.status IN ('proposed', 'approved')) AS pending,
                   (SELECT COUNT(*) FROM information_gaps g
                    WHERE g.case_id = c.case_id
                      AND g.resolved_at IS NULL) AS open_gaps
            FROM cases c
            ORDER BY c.created_at DESC
            LIMIT 200
            """
        )

    by_type: dict[str, dict] = {}
    for r in rows:
        t = r["case_type"] or "debug"
        agg = by_type.setdefault(
            t,
            {"cases": 0, "proposed": 0, "executed": 0, "pending": 0,
             "open_gaps": 0, "cases_with_unresolved_ending": 0},
        )
        agg["cases"] += 1
        agg["proposed"] += int(r["proposed"])
        agg["executed"] += int(r["executed"])
        agg["pending"] += int(r["pending"])
        agg["open_gaps"] += int(r["open_gaps"])
        if r["status"] in ("resolved", "abandoned") and int(r["open_gaps"]) > 0:
            agg["cases_with_unresolved_ending"] += 1

    for agg in by_type.values():
        agg["execution_rate"] = (
            round(agg["executed"] / agg["proposed"], 3)
            if agg["proposed"] else None
        )

    return {"by_case_type": by_type, "cases_scanned": len(rows)}
