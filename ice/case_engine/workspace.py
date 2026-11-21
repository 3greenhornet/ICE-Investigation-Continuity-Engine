"""case_engine/workspace.py — the investigative workspace (global attention).

Owner:      Hari ICE "addition and improvements" pass. Implements layer 4
            of the target architecture ("the new addition I consider
            especially important") plus the attention-portfolio and
            NO_ACTION_REQUIRED directions:
              * candidates from MANY sources compete for attention — not
                just relevance to the latest request, but novelty,
                uncertainty, investigative value and surprise;
              * a PORTFOLIO allocation guarantees that short-term relevance
                cannot consume the whole attention budget (immediate /
                maintenance / development / exploration / speculation);
              * an explicit OBSERVE_ONLY disposition — personal-informatics
                lesson: "do not make every observation generate an action;
                sometimes the correct outcome is leave it alone / keep
                observing / revisit in several months".
Candidate sources (pluggable via _CANDIDATE_SOURCES):
    endogenous.case          — the (expanded) per-case endogenous scan
    endogenous.global        — cross-case connections + dormant gaps
    predictions.due          — predictions awaiting resolution / overdue
    induction.unexplained    — claims no hypothesis explains (curiosity)
Persistence: workspace_items (migration 019) records every surfaced
candidate with its surfacing history, which powers the semantic-refractory
decay — a candidate surfaced recently has its urgency decayed so the same
thought does not crowd the workspace every cycle.
Deps:       db.connection, case_engine (_emit_event), endogenous,
            predictions, induction, retrieval (all lazily — this module
            sits above them in the dependency graph).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from db.connection import get_pool
from case_engine import _emit_event
from case_engine.exceptions import DatabaseUnavailableError

logger = logging.getLogger(__name__)

# Portfolio allocation shares — HONEST NOTE: these defaults are a starting
# hypothesis ("the exact allocations should emerge experimentally"), not
# tuned values. They encode one design commitment: speculation keeps 10% of
# the budget even when immediate work looks more urgent, and maintenance
# (unresolved commitments) keeps 20%.
PORTFOLIO_DEFAULTS = {
    "immediate": 0.50,     # current investigations and obligations
    "maintenance": 0.20,   # unresolved contradictions, aged threads, debt
    "development": 0.15,   # longer-running interests / hypothesis development
    "exploration": 0.10,   # unfamiliar subjects, cross-case connections
    "speculation": 0.05,   # unproven, unusual, high-potential ideas
}

# Candidate-type -> portfolio category mapping.
_CATEGORY_BY_TYPE = {
    "investigation_thread": "maintenance",
    "evidence_review": "maintenance",
    "hypothesis_revisit": "development",
    "contradiction_revisit": "maintenance",
    "unexplained_observation": "development",
    "zero_evidence_hypothesis": "development",
    "dormant_question": "development",
    "prediction_due": "maintenance",
    "cross_case_connection": "exploration",
    "contradiction_cluster": "maintenance",
    "novel_connection": "exploration",
    "contrarian_candidate": "speculation",
    "prediction_failure": "speculation",
}

# Semantic-refractory decay: urgency multiplier for recently surfaced
# candidates. Full urgency returns after _REFRACTORY_HOURS.
_REFRACTORY_HOURS = 12.0


def _category_for(item_type: str) -> str:
    return _CATEGORY_BY_TYPE.get(item_type, "development")


async def _endogenous_case_candidates(case_id: str) -> list[dict]:
    from case_engine.endogenous import collect_case_endogenous_candidates

    try:
        return await collect_case_endogenous_candidates(case_id, current_turn=0)
    except Exception as exc:
        logger.debug("endogenous case scan failed: %s", exc)
        return []


async def _endogenous_global_candidates(case_id: str) -> list[dict]:
    from case_engine.endogenous import collect_global_endogenous_candidates

    try:
        return await collect_global_endogenous_candidates(
            exclude_case_id=case_id
        )
    except Exception as exc:
        logger.debug("endogenous global scan failed: %s", exc)
        return []


async def _unexplained_candidates(case_id: str) -> list[dict]:
    from case_engine.induction import unexplained_observations

    try:
        unexplained = await unexplained_observations(case_id)
    except Exception as exc:
        logger.debug("unexplained scan failed: %s", exc)
        return []
    return [
        {
            "id": f"unexplained_{u['claim_id']}",
            "content": (
                "No current hypothesis explains this observation: "
                f"{u['statement'][:200]}"
            ),
            "item_type": "unexplained_observation",
            "urgency": 0.55,
        }
        for u in unexplained[:5]
    ]


async def _prediction_candidates(case_id: str) -> list[dict]:
    from case_engine.predictions import list_predictions

    try:
        open_preds = await list_predictions(case_id=case_id, status="OPEN")
    except Exception as exc:
        logger.debug("prediction scan failed: %s", exc)
        return []
    out = []
    now = datetime.now(timezone.utc)
    for p in open_preds[:5]:
        age_days = (now - p["created_at"]).total_seconds() / 86400
        urgency = min(1.0, 0.3 + 0.1 * age_days)
        out.append(
            {
                "id": f"prediction_due_{p['prediction_id']}",
                "content": (
                    f"Prediction awaiting resolution (age {age_days:.1f}d): "
                    f"{p['statement'][:180]}"
                ),
                "item_type": "prediction_due",
                "urgency": urgency,
            }
        )
    return out


async def _contrarian_candidates(case_id: str) -> list[dict]:
    """Speculation bucket: the observation-artifact / contrarian directions.

    High-impact low-probability analysis (CIA primer): unlikely but
    consequential explanations must not be silently eliminated by a
    high-probability leader. Surfaces when the case has a strong leader —
    exactly when premature closure risk peaks.
    """
    try:
        from case_engine.hypothesis import (
            list_hypotheses_for_case,
            get_current_version,
        )

        hypotheses = await list_hypotheses_for_case(case_id)
        if not hypotheses:
            return []
        best, best_conf = None, -1.0
        for h in hypotheses:
            try:
                v = await get_current_version(h.hypothesis_id)
            except Exception:
                continue
            if v.confidence > best_conf:
                best, best_conf = h, v.confidence
        if best is None or best_conf < 0.6:
            return []
        return [
            {
                "id": f"contrarian_{best.hypothesis_id}",
                "content": (
                    f"Leading hypothesis at {best_conf:.2f} — challenge its "
                    "key assumption before acting: what if the reported "
                    "observation itself is a measurement artifact, or the "
                    "causal direction is reversed?"
                ),
                "item_type": "contrarian_candidate",
                "urgency": min(0.9, 0.4 + best_conf * 0.4),
            }
        ]
    except Exception as exc:
        logger.debug("contrarian scan failed: %s", exc)
        return []


async def collect_workspace_candidates(case_id: str) -> list[dict]:
    """Gather candidates from every source for one case's workspace pass."""
    candidates: list[dict] = []
    for source in (
        _endogenous_case_candidates,
        _unexplained_candidates,
        _prediction_candidates,
        _contrarian_candidates,
        _endogenous_global_candidates,
    ):
        try:
            candidates.extend(await source(case_id))
        except Exception as exc:
            logger.debug("workspace source %s failed: %s", source.__name__, exc)
    # dedupe by id, keep first
    seen: set[str] = set()
    unique = []
    for c in candidates:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        unique.append(c)
    return unique


def _attention_score(candidate: dict) -> tuple[float, dict]:
    """Attention score with factor breakdown (silent scoring forbidden).

    Factors: urgency (source-computed), novelty (inverse of surfacing
    count — brand-new thoughts get a bump), portfolio pressure (category
    share vs. currently represented share).
    """
    urgency = float(candidate.get("urgency", 0.5))
    surfacing_count = int(candidate.get("surfacing_count", 0))
    novelty = 1.0 / (1.0 + 0.5 * surfacing_count)
    refractory = float(candidate.get("refractory", 1.0))
    score = 0.7 * urgency * refractory + 0.3 * novelty
    factors = {
        "urgency": round(urgency, 3),
        "novelty": round(novelty, 3),
        "surfacing_count": surfacing_count,
        "refractory": round(refractory, 3),
    }
    return round(min(1.0, max(0.0, score)), 4), factors


def _portfolio_select(
    scored: list[dict],
    budget: int,
    shares: dict[str, float],
) -> list[dict]:
    """Select candidates under portfolio category quotas.

    Guarantees each category at least floor(budget × share) slots when it
    has candidates — short-term relevance cannot consume the entire
    attention budget.
    """
    selected: list[dict] = []
    by_cat: dict[str, list[dict]] = {}
    for c in scored:
        by_cat.setdefault(c["category"], []).append(c)

    # quota pass
    taken_ids: set[str] = set()
    for cat, share in sorted(shares.items(), key=lambda kv: -kv[1]):
        quota = int(round(budget * share))
        for c in by_cat.get(cat, [])[:quota]:
            selected.append(c)
            taken_ids.add(c["id"])

    # fill pass (best remaining regardless of category)
    for c in scored:
        if len(selected) >= budget:
            break
        if c["id"] not in taken_ids:
            selected.append(c)
            taken_ids.add(c["id"])
    return selected[:budget]


async def run_workspace_cycle(
    case_id: str,
    budget: int = 7,
    shares: Optional[dict[str, float]] = None,
    auto_observe_threshold: float = 0.25,
) -> dict:
    """One workspace pass for a case: surface, score, allocate, record.

    Returns {
      "selected": [...], "observe_only": [...], "stats": {...}
    }.

    Candidates scoring below auto_observe_threshold are recorded with
    disposition='observe_only' — preserved without demanding action (the
    NO_ACTION_REQUIRED pathway), resurfacing later with refractory-decayed
    urgency.

    Postconditions:
        workspace_items rows upserted for every candidate (surfacing count
        + last_surfaced_at updated); selected top candidates get
        disposition='active' and one workspace_candidate_surfaced event is
        emitted for the selected set; observe_only candidates emit nothing.
    """
    shares = shares or PORTFOLIO_DEFAULTS
    candidates = await collect_workspace_candidates(case_id)
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot run workspace: no DB pool")

    now = datetime.now(timezone.utc)

    # --- load surfacing history for refractory decay ----------------------
    keys = [c["id"] for c in candidates]
    history: dict[str, dict] = {}
    if keys:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT workspace_item_id, candidate_key, surfacing_count, "
                "last_surfaced_at, disposition FROM workspace_items "
                "WHERE candidate_key = ANY($1::text[])",
                keys,
            )
        for r in rows:
            history[r["candidate_key"]] = dict(r)

    scored: list[dict] = []
    for c in candidates:
        h = history.get(c["id"], {})
        surfacing_count = int(h.get("surfacing_count", 0))
        refractory = 1.0
        last = h.get("last_surfaced_at")
        if last is not None:
            hours = (now - last).total_seconds() / 3600.0
            # linear recovery to full urgency over _REFRACTORY_HOURS
            refractory = min(1.0, max(0.15, hours / _REFRACTORY_HOURS))
        c2 = dict(c)
        c2["surfacing_count"] = surfacing_count
        c2["refractory"] = refractory
        c2["category"] = _category_for(c.get("item_type", "thought"))
        score, factors = _attention_score(c2)
        c2["score"] = score
        c2["factors"] = factors
        scored.append(c2)

    scored.sort(key=lambda c: -c["score"])

    selected = _portfolio_select(scored, budget, shares)
    selected_ids = {c["id"] for c in selected}
    observe_only = [
        c for c in scored
        if c["id"] not in selected_ids and c["score"] < auto_observe_threshold
    ]

    # --- persist ------------------------------------------------------------
    import json as _json

    async with pool.acquire() as conn:
        for c in scored:
            disposition = (
                "active" if c["id"] in selected_ids
                else ("observe_only" if c["id"] in {o["id"] for o in observe_only}
                      else "dormant")
            )
            await conn.execute(
                """
                INSERT INTO workspace_items (
                    workspace_item_id, case_id, candidate_key, category,
                    item_type, content, score, factors, disposition,
                    surfacing_count, last_surfaced_at, origin, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9,
                    $10, $11, $12, $13
                )
                ON CONFLICT (candidate_key) DO UPDATE SET
                    score = EXCLUDED.score,
                    factors = EXCLUDED.factors,
                    category = EXCLUDED.category,
                    disposition = EXCLUDED.disposition,
                    surfacing_count = workspace_items.surfacing_count + 1,
                    last_surfaced_at = EXCLUDED.last_surfaced_at,
                    updated_at = EXCLUDED.updated_at
                """,
                f"ws_{uuid.uuid4().hex[:12]}",
                case_id,
                c["id"],
                c["category"],
                c.get("item_type", "thought"),
                c["content"][:1000],
                c["score"],
                _json.dumps(c["factors"], default=str),
                disposition,
                1,
                now,
                c.get("source", "endogenous"),
                now,
            )

    if selected:
        await _emit_event(
            case_id,
            "workspace_candidate_surfaced",
            {
                "case_id": case_id,
                "selected": [
                    {
                        "candidate_key": c["id"],
                        "category": c["category"],
                        "score": c["score"],
                        "factors": c["factors"],
                    }
                    for c in selected
                ],
                "observe_only_count": len(observe_only),
            },
        )

    return {
        "selected": [
            {
                "candidate_key": c["id"],
                "category": c["category"],
                "item_type": c.get("item_type"),
                "content": c["content"][:300],
                "score": c["score"],
                "factors": c["factors"],
            }
            for c in selected
        ],
        "observe_only": [
            {"candidate_key": c["id"], "content": c["content"][:200]}
            for c in observe_only
        ],
        "stats": {
            "candidates_total": len(scored),
            "selected": len(selected),
            "observe_only": len(observe_only),
            "by_category": {
                cat: sum(1 for c in selected if c["category"] == cat)
                for cat in shares
            },
        },
    }


async def get_workspace(case_id: str, disposition: Optional[str] = None) -> list[dict]:
    """Read the persisted workspace items for a case."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    if disposition:
        sql = (
            "SELECT * FROM workspace_items WHERE case_id = $1 "
            "AND disposition = $2 ORDER BY score DESC"
        )
        params = (case_id, disposition)
    else:
        sql = "SELECT * FROM workspace_items WHERE case_id = $1 ORDER BY score DESC"
        params = (case_id,)
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [
        {
            "workspace_item_id": r["workspace_item_id"],
            "candidate_key": r["candidate_key"],
            "category": r["category"],
            "item_type": r["item_type"],
            "content": r["content"],
            "score": float(r["score"]),
            "factors": r["factors"],
            "disposition": r["disposition"],
            "surfacing_count": r["surfacing_count"],
            "last_surfaced_at": r["last_surfaced_at"],
        }
        for r in rows
    ]


async def set_workspace_disposition(
    workspace_item_id: str, disposition: str
) -> None:
    """Human override: action / dismiss / observe a workspace candidate."""
    if disposition not in ("active", "observe_only", "actioned", "dismissed", "dormant"):
        raise ValueError(f"invalid disposition: {disposition}")
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE workspace_items SET disposition = $1, updated_at = $2 "
            "WHERE workspace_item_id = $3",
            disposition, datetime.now(timezone.utc), workspace_item_id,
        )
