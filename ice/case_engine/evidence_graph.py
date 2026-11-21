"""case_engine/evidence_graph.py — typed graph over the case's entities.

Owner: investigation logic layer (Part 3, section 4).
Purpose: maintain and traverse the ``evidence_graph_edges`` table linking
evidence, claims, hypotheses, gaps, and actions with typed relations
(SUPPORTS, CONTRADICTS, REFINES, SUPERSEDES, DERIVED_FROM, QUESTIONS,
DISCRIMINATES, BLOCKS). Powers the Decision Debugger UI's hypothesis
subgraph and generic BFS traversal.

Downstream dependencies (allowed):
    - db.connection.get_pool
    - case_engine.exceptions (typed errors)
    - case_engine.schemas (Evidence, Claim, Hypothesis)
    - case_engine.evidence (public: get_evidence)
    - case_engine.claim (public: get_claim)
    - case_engine.hypothesis (public: get_hypothesis, get_current_version,
      get_version_history)
"""

import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from db.connection import get_pool
from case_engine.exceptions import DatabaseUnavailableError
from case_engine.schemas import Evidence, Claim, Hypothesis
from case_engine.evidence import get_evidence
from case_engine.claim import get_claim
from case_engine.hypothesis import get_hypothesis, get_current_version, get_version_history

logger = logging.getLogger(__name__)


class EdgeRelation(str, Enum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    REFINES = "REFINES"
    SUPERSEDES = "SUPERSEDES"
    DERIVED_FROM = "DERIVED_FROM"
    QUESTIONS = "QUESTIONS"
    DISCRIMINATES = "DISCRIMINATES"
    BLOCKS = "BLOCKS"


NodeType = Literal["evidence", "claim", "hypothesis", "gap", "action"]
TargetType = Literal["claim", "hypothesis", "gap", "action", "evidence"]


async def add_edge(
    case_id: str,
    from_node: str,
    from_type: NodeType,
    to_node: str,
    to_type: TargetType,
    relation: EdgeRelation,
    weight: float = 1.0,
    metadata: Optional[dict] = None,
) -> str:
    """Add a typed edge. Idempotent on (from_node, to_node, relation).

    LIMITATION (documented, not a bug — doc Part 2 §11.2, cleanup 2J): the
    schema (013_evidence_graph.sql) has UNIQUE(from_node, to_node,
    relation), which means a claim can support the same hypothesis with at
    most one SUPPORTS edge. If you need to record two different reasons a
    claim supports a hypothesis, use the edge's metadata dict (e.g.,
    metadata={"reasons": ["timing", "mechanism"]}). Alternatively, model
    the two reasons as two separate claims. Both patterns are supported;
    the schema limitation is deliberate to keep the graph a simple DAG.

    Postconditions:
        Row exists in evidence_graph_edges. If a row with the same triple
        already exists, returns the existing edge_id without modification.
    Raises:
        ValueError on bad weight.
        DatabaseUnavailableError.
    Side effects:
        INSERT INTO evidence_graph_edges (0 or 1 row).
    """
    if not (0.0 <= weight <= 1.0):
        raise ValueError("weight must be in [0, 1]")

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot add edge: no DB pool")

    import json as _json
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            """
            SELECT edge_id FROM evidence_graph_edges
            WHERE from_node = $1 AND to_node = $2 AND relation = $3
            """,
            from_node, to_node, relation.value,
        )
        if existing:
            return existing["edge_id"]

        edge_id = f"edge_{uuid.uuid4().hex[:12]}"
        await conn.execute(
            """
            INSERT INTO evidence_graph_edges (
                edge_id, case_id, from_node, from_type,
                to_node, to_type, relation, weight, metadata, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
            """,
            edge_id, case_id, from_node, from_type,
            to_node, to_type, relation.value, weight,
            _json.dumps(metadata or {}), datetime.now(timezone.utc),
        )
        return edge_id


async def get_supporting_evidence(
    hypothesis_id: str,
    max_depth: int = 3,
) -> list[Evidence]:
    """Traverse SUPPORTS edges backwards to find evidence.

    Algorithm:
        depth 0: claims with SUPPORTS edges to hypothesis_id.
        For each claim, collect claim.supporting_evidence_ids.
        Evidence cannot "support evidence". Only claims support hypotheses.
        So this function is depth-agnostic (max_depth used only for future
        extension where claims support claims).

    Postconditions:
        Returns the union of supporting_evidence_ids across all supporting claims.
        Order: descending by quality_score.
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot traverse: no DB pool")

    async with pool.acquire() as conn:
        claim_ids = [
            row["from_node"] for row in await conn.fetch(
                """
                SELECT from_node FROM evidence_graph_edges
                WHERE to_node = $1 AND from_type = 'claim' AND relation = 'SUPPORTS'
                """,
                hypothesis_id,
            )
        ]

    evidence_ids: set[str] = set()
    for cid in claim_ids:
        try:
            claim = await get_claim(cid)
        except Exception:
            continue
        evidence_ids.update(claim.supporting_evidence_ids or [])

    results: list[Evidence] = []
    for eid in evidence_ids:
        try:
            results.append(await get_evidence(eid))
        except Exception:
            continue
    results.sort(key=lambda e: e.quality_score, reverse=True)
    return results


async def get_contradicting_evidence(
    hypothesis_id: str,
    max_depth: int = 3,
) -> list[Evidence]:
    """Symmetric to get_supporting_evidence but for CONTRADICTS edges.

    Collects contradicting_evidence_ids across all contradicting claims and
    returns the loaded Evidence items sorted by quality_score DESC.
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot traverse: no DB pool")

    async with pool.acquire() as conn:
        claim_ids = [
            row["from_node"] for row in await conn.fetch(
                """
                SELECT from_node FROM evidence_graph_edges
                WHERE to_node = $1 AND from_type = 'claim' AND relation = 'CONTRADICTS'
                """,
                hypothesis_id,
            )
        ]

    evidence_ids: set[str] = set()
    for cid in claim_ids:
        try:
            claim = await get_claim(cid)
        except Exception:
            continue
        evidence_ids.update(claim.contradicting_evidence_ids or [])

    results: list[Evidence] = []
    for eid in evidence_ids:
        try:
            results.append(await get_evidence(eid))
        except Exception:
            continue
    results.sort(key=lambda e: e.quality_score, reverse=True)
    return results


async def get_evidence_chain(evidence_id: str) -> list[dict]:
    """Return the full provenance chain for an evidence item.

    Postconditions:
        Returns a list of dicts, one per hop, ordered root -> leaf.
        Each entry: {"node_id", "node_type", "relation", "metadata"}.
        Starts with the evidence itself (relation SELF) and its source
        (relation DERIVED_FROM), then appends any claim with a DERIVED_FROM
        edge pointing at the evidence, then any hypothesis connected via
        SUPPORTS/CONTRADICTS edges touching the evidence.
    Raises: EvidenceNotFoundError when the evidence id is unknown.
    Side effects: none.
    """
    evidence = await get_evidence(evidence_id)
    chain: list[dict] = [
        {
            "node_id": evidence.evidence_id,
            "node_type": "evidence",
            "relation": "SELF",
            "metadata": {
                "evidence_type": evidence.evidence_type,
                "status": evidence.status,
                "quality_score": evidence.quality_score,
            },
        },
        {
            "node_id": evidence.source_id,
            "node_type": "source",
            "relation": "DERIVED_FROM",
            "metadata": {},
        },
    ]

    pool = await get_pool()
    if pool is None:
        return chain

    async with pool.acquire() as conn:
        claim_edges = await conn.fetch(
            """
            SELECT from_node FROM evidence_graph_edges
            WHERE to_node = $1 AND from_type = 'claim' AND relation = 'DERIVED_FROM'
            """,
            evidence_id,
        )
        hypothesis_edges = await conn.fetch(
            """
            SELECT from_node, to_node FROM evidence_graph_edges
            WHERE (from_node = $1 OR to_node = $1)
              AND relation IN ('SUPPORTS', 'CONTRADICTS')
            """,
            evidence_id,
        )

    for row in claim_edges:
        chain.append({
            "node_id": row["from_node"],
            "node_type": "claim",
            "relation": "DERIVED_FROM",
            "metadata": {},
        })

    for row in hypothesis_edges:
        chain.append({
            "node_id": row["to_node"] if row["from_node"] == evidence_id else row["from_node"],
            "node_type": "hypothesis",
            "relation": "SUPPORTS_OR_CONTRADICTS",
            "metadata": {},
        })

    return chain


async def get_hypothesis_graph(hypothesis_id: str) -> dict:
    """Return the full subgraph for a hypothesis (used by Decision Debugger UI).

    Postconditions:
        Returns a dict:
        {
            "hypothesis_id": str,
            "statement": str,
            "status": str,
            "current_confidence": float,
            "supporting_claims": [{"claim_id", "statement", "claim_type",
                                    "confidence", "evidence": [...]}],
            "contradicting_claims": [same shape],
            "version_history": [{"version_number", "confidence",
                                 "prior_confidence", "reason", "created_at"}]
        }
    Raises: HypothesisNotFoundError, DatabaseUnavailableError.
    Side effects: none.
    """
    hypothesis = await get_hypothesis(hypothesis_id)
    current = await get_current_version(hypothesis_id)

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot build graph: no DB pool")

    async with pool.acquire() as conn:
        incoming = await conn.fetch(
            """
            SELECT from_node, relation FROM evidence_graph_edges
            WHERE to_node = $1 AND from_type = 'claim'
            """,
            hypothesis_id,
        )

    supporting: list[dict] = []
    contradicting: list[dict] = []

    for edge in incoming:
        try:
            claim = await get_claim(edge["from_node"])
        except Exception:
            continue

        evidence_list: list[dict] = []
        for eid in claim.supporting_evidence_ids:
            try:
                ev = await get_evidence(eid)
                evidence_list.append({
                    "evidence_id": ev.evidence_id,
                    "content_preview": ev.content[:200],
                    "evidence_type": ev.evidence_type,
                    "source_id": ev.source_id,
                    "status": ev.status,
                })
            except Exception:
                continue

        entry = {
            "claim_id": claim.claim_id,
            "statement": claim.statement,
            "claim_type": claim.claim_type,
            "confidence": claim.confidence,
            "evidence": evidence_list,
        }
        if edge["relation"] == "SUPPORTS":
            supporting.append(entry)
        elif edge["relation"] == "CONTRADICTS":
            contradicting.append(entry)

    history = await get_version_history(hypothesis_id)

    return {
        "hypothesis_id": hypothesis_id,
        "statement": hypothesis.statement,
        "status": hypothesis.status,
        "current_confidence": current.confidence,
        "supporting_claims": supporting,
        "contradicting_claims": contradicting,
        "version_history": [
            {
                "version_number": v.version_number,
                "confidence": v.confidence,
                "prior_confidence": v.prior_confidence,
                "reason": v.reason,
                "created_at": v.created_at.isoformat(),
            }
            for v in history
        ],
    }


async def traverse(
    case_id: str,
    start_node: str,
    relation_types: Optional[list[EdgeRelation]] = None,
    max_depth: int = 3,
) -> list[dict]:
    """Generic BFS traversal over evidence_graph_edges.

    Postconditions:
        Returns visited nodes as dicts:
        {"node_id", "node_type", "depth", "path_relation"}.
        Start node is depth 0.
        Respects max_depth.
        Does not revisit nodes (cycle-safe).
    Raises: DatabaseUnavailableError.
    Side effects: none (read-only).
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot traverse: no DB pool")

    relations = [r.value for r in relation_types] if relation_types else None
    visited: set[str] = set()
    queue: list[tuple[str, int, Optional[str]]] = [(start_node, 0, None)]
    results: list[dict] = []
    while queue:
        node_id, depth, path_rel = queue.pop(0)
        if node_id in visited:
            continue
        visited.add(node_id)

        async with pool.acquire() as conn:
            node_row = await conn.fetchrow(
                """
                SELECT from_type FROM evidence_graph_edges WHERE from_node = $1 LIMIT 1
                """,
                node_id,
            )
            if node_row is None:
                node_row = await conn.fetchrow(
                    """
                    SELECT to_type AS from_type FROM evidence_graph_edges
                    WHERE to_node = $1 LIMIT 1
                    """,
                    node_id,
                )

        results.append({
            "node_id": node_id,
            "node_type": node_row["from_type"] if node_row else "unknown",
            "depth": depth,
            "path_relation": path_rel,
        })

        if depth >= max_depth:
            continue

        async with pool.acquire() as conn:
            if relations:
                edges = await conn.fetch(
                    """
                    SELECT from_node, to_node, relation FROM evidence_graph_edges
                    WHERE case_id = $1
                      AND (from_node = $2 OR to_node = $2)
                      AND relation = ANY($3::text[])
                    """,
                    case_id, node_id, relations,
                )
            else:
                edges = await conn.fetch(
                    """
                    SELECT from_node, to_node, relation FROM evidence_graph_edges
                    WHERE case_id = $1 AND (from_node = $2 OR to_node = $2)
                    """,
                    case_id, node_id,
                )

        for edge in edges:
            neighbor = edge["to_node"] if edge["from_node"] == node_id else edge["from_node"]
            if neighbor not in visited:
                queue.append((neighbor, depth + 1, edge["relation"]))

    return results