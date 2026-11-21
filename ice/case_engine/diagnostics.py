"""case_engine/diagnostics.py — EvalMem-lite memory operation diagnostics.

Owner:      Hari ICE "addition and improvements" pass. The EvalMem finding:
            "Imagine that ICE fails to remember an important observation.
            There are three entirely different possible causes: 1. It never
            extracted the observation (encoding). 2. It extracted it but
            couldn't retrieve it (retrieval). 3. It retrieved it, but its
            reasoning ignored it (reasoning). Adding another LLM to the
            reasoning pipeline only helps with the third problem."
            This module separates the three failure modes BEFORE any new
            memory architecture is built — the audit's explicit
            recommendation ("implement an EvalMem-style diagnostic layer
            before building a new memory architecture").
Deps:       db.connection, case_engine.retrieval, case_engine.claim
            (read-only).
"""

from __future__ import annotations

import logging
import re

from db.connection import get_pool
from case_engine.exceptions import DatabaseUnavailableError

logger = logging.getLogger(__name__)


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2
    }


async def check_encoding(case_id: str, expected_fact: str) -> dict:
    """Was this fact ever STORED (as a claim or evidence content)?

    Encoding failure = the information never entered the knowledge base.
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    fact_tokens = _tokens(expected_fact)
    if not fact_tokens:
        return {"stored": False, "match": None, "coverage": 0.0}

    async with pool.acquire() as conn:
        claims = await conn.fetch(
            "SELECT claim_id, statement FROM claims WHERE case_id = $1",
            case_id,
        )
        evidence = await conn.fetch(
            "SELECT evidence_id, content FROM evidence WHERE case_id = $1",
            case_id,
        )

    best_claim, best_cov = None, 0.0
    for r in claims:
        cov = len(fact_tokens & _tokens(r["statement"])) / len(fact_tokens)
        if cov > best_cov:
            best_claim, best_cov = r["claim_id"], cov
    best_evidence, best_ecov = None, 0.0
    for r in evidence:
        cov = len(fact_tokens & _tokens(r["content"])) / len(fact_tokens)
        if cov > best_ecov:
            best_evidence, best_ecov = r["evidence_id"], cov

    stored = best_cov >= 0.5 or best_ecov >= 0.5
    return {
        "stored": stored,
        "best_claim": {"claim_id": best_claim, "coverage": round(best_cov, 3)}
        if best_claim else None,
        "best_evidence": {
            "evidence_id": best_evidence, "coverage": round(best_ecov, 3)
        } if best_evidence else None,
        "threshold": 0.5,
    }


async def check_retrieval(case_id: str, expected_fact: str, top_k: int = 5) -> dict:
    """Can the retrieval layer FIND the stored fact when needed?"""
    from case_engine.retrieval import search_evidence, search_claims

    ev_hits = await search_evidence(expected_fact, case_id=case_id, limit=top_k)
    cl_hits = await search_claims(expected_fact, case_id=case_id, limit=top_k)
    found = bool(ev_hits or cl_hits)
    return {
        "retrievable": found,
        "top_evidence": [
            {"evidence_id": h.item_id, "score": h.score} for h in ev_hits[:3]
        ],
        "top_claims": [
            {"claim_id": h.item_id, "score": h.score} for h in cl_hits[:3]
        ],
    }


async def check_reasoning(case_id: str, expected_fact: str) -> dict:
    """Is the fact CONNECTED to any hypothesis (used by the reasoning)?"""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    fact_tokens = _tokens(expected_fact)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT h.hypothesis_id, h.statement, c.claim_id, c.statement AS claim_stmt
            FROM claims c
            JOIN evidence_graph_edges e
              ON e.from_node = c.claim_id AND e.from_type = 'claim'
            JOIN hypotheses h ON h.hypothesis_id = e.to_node
            WHERE c.case_id = $1
            """,
            case_id,
        )
    connected = []
    for r in rows:
        cov = len(fact_tokens & _tokens(r["claim_stmt"])) / max(1, len(fact_tokens))
        if cov >= 0.5:
            connected.append(
                {
                    "claim_id": r["claim_id"],
                    "hypothesis_id": r["hypothesis_id"],
                    "coverage": round(cov, 3),
                }
            )
    return {
        "connected_to_reasoning": bool(connected),
        "connections": connected[:5],
    }


async def memory_diagnostic_report(
    case_id: str,
    expected_facts: list[str],
) -> dict:
    """Classify each expected fact's failure mode (encode/retrieve/reason).

    The output is the decision table for memory work: encoding failures
    need better extraction; retrieval failures need better search;
    reasoning failures (only these) justify more reasoning machinery.
    """
    results = []
    for fact in expected_facts:
        encoding = await check_encoding(case_id, fact)
        retrieval = await check_retrieval(case_id, fact)
        reasoning = await check_reasoning(case_id, fact)
        if not encoding["stored"]:
            failure = "encoding"
        elif not retrieval["retrievable"]:
            failure = "retrieval"
        elif not reasoning["connected_to_reasoning"]:
            failure = "reasoning"
        else:
            failure = None
        results.append(
            {
                "fact": fact,
                "encoding": encoding,
                "retrieval": retrieval,
                "reasoning": reasoning,
                "failure_mode": failure,
            }
        )

    counts = {
        mode: sum(1 for r in results if r["failure_mode"] == mode)
        for mode in ("encoding", "retrieval", "reasoning", None)
    }
    return {
        "case_id": case_id,
        "facts_checked": len(results),
        "failure_counts": counts,
        "recommendation": (
            "encoding failures -> improve claim extraction; retrieval "
            "failures -> improve hybrid search; reasoning failures (only "
            "these) justify additional reasoning machinery"
        ),
        "per_fact": results,
    }
