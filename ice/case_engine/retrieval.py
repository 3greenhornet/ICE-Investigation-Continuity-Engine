"""case_engine/retrieval.py — general-purpose hybrid evidence/claim retrieval.

Owner:      Hari ICE "addition and improvements" pass (second-pass audit
            gaps: "no comparable general-purpose evidence query/retrieval
            API — the RETRIEVE action has no proper foundation" and
            "cross-case retrieval: connections across investigations are
            missed").
Purpose:    A unified search layer over the ICE evidence and claims tables,
            adapting the hybrid retrieval pattern already proven in
            engine/memory.retrieve_candidates_hybrid (vector similarity +
            PostgreSQL full-text ts_rank) to the ICE data model, with:
              * deterministic lexical fallback when embeddings are
                unavailable (no embedding provider key → the search still
                works, just lexical-only; never raises because of a
                missing embedding API);
              * Reciprocal Rank Fusion of the vector and lexical rankings;
              * case filtering, cross-case search, temporal filtering and
                source-lineage annotation;
              * three retrieval STRATEGIES beyond similarity (the
                "dimensions we have been overlooking" section):
                  - similarity  (default): find related material
                  - contrastive: find material that CHALLENGES a statement
                    (deliberately seek counterevidence rather than
                    confirmation)
                  - associative: follow evidence_graph edges 1-2 hops from
                    anchor items (cross semantic boundaries through
                    intermediate concepts)
Deps:       db.connection, case_engine.exceptions (read-only module: emits
            no events, performs no mutations).

Honesty note: cosine similarity requires populated embeddings. Evidence
ingested without an embedding provider is still searchable via the lexical
channel (ts_rank_cd over to_tsvector('english', content)) and a trigram-ish
fallback when even FTS yields nothing. The fusion is deterministic.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from db.connection import get_pool
from case_engine.exceptions import DatabaseUnavailableError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass
class RetrievalHit:
    """One retrieved item with its fused score and per-channel rankings."""

    item_type: str            # "evidence" | "claim"
    item_id: str
    case_id: str
    content: str
    score: float = 0.0
    vector_rank: Optional[int] = None
    lexical_rank: Optional[int] = None
    evidence_type: Optional[str] = None
    claim_type: Optional[str] = None
    source_id: Optional[str] = None
    source_lineage_id: Optional[str] = None
    observed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    relation_to_anchor: Optional[str] = None  # associative mode only
    factors: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "item_type": self.item_type,
            "item_id": self.item_id,
            "case_id": self.case_id,
            "content": self.content,
            "score": round(self.score, 4),
            "vector_rank": self.vector_rank,
            "lexical_rank": self.lexical_rank,
            "evidence_type": self.evidence_type,
            "claim_type": self.claim_type,
            "source_lineage_id": self.source_lineage_id,
            "relation_to_anchor": self.relation_to_anchor,
            "factors": self.factors,
        }


# ---------------------------------------------------------------------------
# Lexical helpers (deterministic, no external deps)
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


_STOPWORDS = frozenset(
    "the a an and or but if then of to in on for with at by from as is are was "
    "were be been being it its this that these those not no do does did done "
    "we i you they he she them there here what which who when where why how "
    "all any both each few more most other some such only own same so than "
    "too very can will just should now".split()
)


def _content_tokens(text: str) -> list[str]:
    return [t for t in _tokens(text) if t not in _STOPWORDS and len(t) > 2]


def _lexical_score(query_terms: list[str], content: str) -> float:
    """Deterministic coverage score: fraction of query terms present."""
    if not query_terms:
        return 0.0
    doc_terms = set(_content_tokens(content))
    hit = sum(1 for t in query_terms if t in doc_terms)
    return hit / len(query_terms)


def _rrf_fuse(
    vector_ranking: list[str],
    lexical_ranking: list[str],
    k: int = 60,
) -> dict[str, tuple[float, Optional[int], Optional[int]]]:
    """Reciprocal Rank Fusion over two id-ordered rankings.

    Returns {item_id: (fused_score, vector_rank, lexical_rank)}.
    """
    fused: dict[str, tuple[float, Optional[int], Optional[int]]] = {}
    for rank, item_id in enumerate(vector_ranking, start=1):
        score, vr, lr = fused.get(item_id, (0.0, None, None))
        fused[item_id] = (score + 1.0 / (k + rank), rank, lr)
    for rank, item_id in enumerate(lexical_ranking, start=1):
        score, vr, lr = fused.get(item_id, (0.0, None, None))
        fused[item_id] = (score + 1.0 / (k + rank), vr, rank)
    return fused


# ---------------------------------------------------------------------------
# Core search
# ---------------------------------------------------------------------------


async def _embed_query(query: str) -> Optional[list[float]]:
    """Embed a query via the substrate, returning None on any failure.

    Deterministic retrieval must not depend on an embedding provider being
    reachable — the lexical channel keeps the search functional.
    """
    try:
        from engine.memory import embed

        vec = await embed(query)
        return list(vec) if vec else None
    except Exception as exc:
        logger.debug("query embedding unavailable (%s); lexical-only search", exc)
        return None


MIN_VECTOR_SIMILARITY = 0.58


async def search_evidence(
    query: str,
    case_id: Optional[str] = None,
    cross_case: bool = False,
    limit: int = 10,
    valid_at: Optional[datetime] = None,
    include_stale: bool = False,
    min_similarity: Optional[float] = None,
) -> list[RetrievalHit]:
    """Hybrid search over ACTIVE evidence rows.

    Args:
        query: natural-language query.
        case_id: restrict to one case. When None and cross_case is False,
            every case is searched (the caller filters what it needs).
        cross_case: when True WITH case_id given, OTHER cases are searched
            (excluding the given case) — the cross-case connection finder.
        valid_at: when given, only evidence whose [valid_from, valid_until]
            window covers the timestamp (NULL bounds treated as open).
        limit: maximum number of hits returned.
        include_stale: include STALE/SUPERSEDED rows (default: ACTIVE only).

    Raises:
        DatabaseUnavailableError.
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot search evidence: no DB pool")

    conditions = ["TRUE"]
    params: list = []

    if case_id and not cross_case:
        params.append(case_id)
        conditions.append(f"e.case_id = ${len(params)}")
    elif case_id and cross_case:
        params.append(case_id)
        conditions.append(f"e.case_id <> ${len(params)}")
    if not include_stale:
        conditions.append("e.status = 'ACTIVE'")

    where = " AND ".join(conditions)

    # --- vector channel ---------------------------------------------------
    # NOTE: every later parameter (embedding, query, limits) is appended to
    # the SAME params list so the $N numbering stays collision-free — an
    # earlier draft hardcoded $1/$2 for the channel parameters while
    # `where` already used $1 for the case filter, silently breaking both
    # channels whenever a case filter was active.
    vector_ranking: list[str] = []
    vector_similarity: dict[str, float] = {}
    sim_floor = MIN_VECTOR_SIMILARITY if min_similarity is None else float(min_similarity)
    embedding = await _embed_query(query)
    if embedding is not None:
        try:
            async with pool.acquire() as conn:
                vparams = params + [embedding, sim_floor, max(limit * 3, 20)]
                rows = await conn.fetch(
                    f"""
                    SELECT e.evidence_id,
                           (1.0 - (e.embedding <=> ${len(params) + 1}::vector)) AS similarity
                    FROM evidence e
                    WHERE {where}
                      AND e.embedding IS NOT NULL
                      AND (1.0 - (e.embedding <=> ${len(params) + 1}::vector)) >= ${len(params) + 2}
                    ORDER BY e.embedding <=> ${len(params) + 1}::vector
                    LIMIT ${len(params) + 3}
                    """,
                    *vparams,
                )
            vector_ranking = [r["evidence_id"] for r in rows]
            vector_similarity = {
                r["evidence_id"]: float(r["similarity"]) for r in rows if r["similarity"] is not None
            }
        except Exception as exc:
            logger.debug("vector channel failed: %s", exc)

    # --- lexical channel (ts_rank_cd) --------------------------------------
    lexical_ranking: list[str] = []
    try:
        async with pool.acquire() as conn:
            lparams = params + [query, max(limit * 3, 20)]
            rows = await conn.fetch(
                f"""
                SELECT e.evidence_id,
                       ts_rank_cd(
                           to_tsvector('english', e.content),
                           plainto_tsquery('english', ${len(params) + 1})
                       ) AS rank
                FROM evidence e
                WHERE {where}
                  AND to_tsvector('english', e.content) @@
                      plainto_tsquery('english', ${len(params) + 1})
                ORDER BY rank DESC
                LIMIT ${len(params) + 2}
                """,
                *lparams,
            )
            lexical_ranking = [r["evidence_id"] for r in rows]
    except Exception as exc:
        logger.debug("lexical FTS channel failed: %s", exc)

    # --- deterministic in-Python fallback when both channels empty --------
    ids = list(dict.fromkeys(vector_ranking + lexical_ranking))
    if not ids:
        query_terms = _content_tokens(query)
        if query_terms:
            async with pool.acquire() as conn:
                params2 = list(params)
                extra = ""
                if case_id and not cross_case:
                    extra = " AND e.case_id = $1"
                elif case_id and cross_case:
                    extra = " AND e.case_id <> $1"
                status_filter = "" if include_stale else " AND e.status = 'ACTIVE'"
                rows = await conn.fetch(
                    f"SELECT e.evidence_id, e.content FROM evidence e "
                    f"WHERE TRUE{extra}{status_filter} LIMIT 500",
                    *params2,
                )
            scored = [
                (r["evidence_id"], _lexical_score(query_terms, r["content"]))
                for r in rows
            ]
            scored = [s for s in scored if s[1] > 0]
            scored.sort(key=lambda x: (-x[1], x[0]))
            lexical_ranking = [i for i, _ in scored[: max(limit * 3, 20)]]
            ids = list(lexical_ranking)

    if not ids:
        return []

    fused = _rrf_fuse(vector_ranking, lexical_ranking)
    ranked_ids = sorted(fused.items(), key=lambda kv: (-kv[1][0], kv[0]))

    # --- materialize top hits ---------------------------------------------
    hits: list[RetrievalHit] = []
    query_terms = _content_tokens(query)
    async with pool.acquire() as conn:
        for item_id, (score, vr, lr) in ranked_ids:
            if len(hits) >= limit:
                break
            row = await conn.fetchrow(
                """
                SELECT e.evidence_id, e.case_id, e.content, e.evidence_type,
                       e.source_id, e.source_lineage_id, e.observed_at,
                       e.recorded_at, e.valid_from, e.valid_until, e.status
                FROM evidence e WHERE e.evidence_id = $1
                """,
                item_id,
            )
            if row is None:
                continue
            if valid_at is not None:
                vf, vu = row["valid_from"], row["valid_until"]
                if vf is not None and valid_at < vf:
                    continue
                if vu is not None and valid_at > vu:
                    continue
            hits.append(
                RetrievalHit(
                    item_type="evidence",
                    item_id=row["evidence_id"],
                    case_id=row["case_id"],
                    content=row["content"],
                    score=score,
                    vector_rank=vr,
                    lexical_rank=lr,
                    evidence_type=row["evidence_type"],
                    source_id=row["source_id"],
                    source_lineage_id=row["source_lineage_id"],
                    observed_at=row["observed_at"],
                    created_at=row["recorded_at"],
                    factors={
                        "fused_rrf": round(score, 5),
                        "lexical_coverage": round(
                            _lexical_score(query_terms, row["content"]), 3
                        ),
                        **(
                            {"vector_similarity": round(vector_similarity[item_id], 4)}
                            if item_id in vector_similarity
                            else {}
                        ),
                    },
                )
            )
    return hits


async def search_claims(
    query: str,
    case_id: Optional[str] = None,
    limit: int = 10,
    active_only: bool = True,
) -> list[RetrievalHit]:
    """Lexical search over claim statements (claims carry no embeddings)."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot search claims: no DB pool")

    query_terms = _content_tokens(query)
    conditions = ["TRUE"]
    params: list = []
    if case_id:
        params.append(case_id)
        conditions.append(f"c.case_id = ${len(params)}")
    if active_only:
        conditions.append("c.status = 'ACTIVE'")
    where = " AND ".join(conditions)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT c.claim_id, c.case_id, c.statement, c.claim_type,
                   c.confidence, c.created_at
            FROM claims c
            WHERE {where}
            LIMIT 800
            """,
            *params,
        )

    scored: list[tuple[float, str, dict]] = []
    for r in rows:
        lex = _lexical_score(query_terms, r["statement"])
        # FTS channel when available
        scored.append((lex, r["claim_id"], dict(r)))
    scored = [s for s in scored if s[0] > 0 or not query_terms]
    scored.sort(key=lambda x: (-x[0], x[1]))

    hits: list[RetrievalHit] = []
    for lex, claim_id, row in scored[:limit]:
        hits.append(
            RetrievalHit(
                item_type="claim",
                item_id=claim_id,
                case_id=row["case_id"],
                content=row["statement"],
                score=lex,
                lexical_rank=None,
                claim_type=row["claim_type"],
                created_at=row["created_at"],
                factors={"lexical_coverage": round(lex, 3)},
            )
        )
    return hits


# ---------------------------------------------------------------------------
# Contrastive retrieval — deliberately seek material that CHALLENGES
# ---------------------------------------------------------------------------

_NEGATION_CUES = (
    "not", "no", "never", "without", "unrelated", "unchanged", "normal",
    "stable", "healthy", "decreased", "below", "insignificant", "ruled out",
    "excluded", "denied", "disproves", "contradicts", "instead",
)


def build_contrastive_query(statement: str) -> str:
    """Derive a query that surfaces material OPPOSED to ``statement``.

    Deterministic heuristic: strip assertive verbs, attach contrast cues for
    the salient terms. E.g. "connection pool exhaustion caused the latency"
    → "connection pool latency unchanged normal not ruled out".
    """
    terms = _content_tokens(statement)
    salient = [t for t in terms if t not in ("caused", "causes", "was",
                                             "is", "the", "led", "to")]
    return " ".join(salient + list(_NEGATION_CUES[:8]))


async def contrastive_search(
    statement: str,
    case_id: str,
    limit: int = 8,
) -> list[RetrievalHit]:
    """Find evidence/claims that challenge ``statement``.

    Combines: (a) lexical search with contrast cues, (b) claims already
    linked CONTRADICTS to any hypothesis resembling the statement,
    (c) evidence whose content contains negation cues overlapping the
    statement's salient terms. Supporting echo chambers is the failure mode
    this exists to prevent.
    """
    contrast_query = build_contrastive_query(statement)
    hits = await search_evidence(contrast_query, case_id=case_id, limit=limit)

    # Channel (b): explicit CONTRADICTS edges on similar hypotheses.
    try:
        pool = await get_pool()
        if pool is not None:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT c.claim_id, c.case_id, c.statement,
                           c.claim_type, c.created_at
                    FROM claims c
                    JOIN evidence_graph_edges e
                      ON (e.from_node = c.claim_id AND e.from_type = 'claim')
                    JOIN hypotheses h ON h.hypothesis_id = e.to_node
                    WHERE h.case_id = $1
                      AND e.relation = 'CONTRADICTS'
                      AND c.status = 'ACTIVE'
                    LIMIT $2
                    """,
                    case_id, limit,
                )
            existing_ids = {h.item_id for h in hits}
            for r in rows:
                if r["claim_id"] in existing_ids:
                    continue
                overlap = _lexical_score(
                    _content_tokens(statement), r["statement"]
                )
                hits.append(
                    RetrievalHit(
                        item_type="claim",
                        item_id=r["claim_id"],
                        case_id=r["case_id"],
                        content=r["statement"],
                        score=0.5 + 0.5 * overlap,
                        claim_type=r["claim_type"],
                        created_at=r["created_at"],
                        factors={"channel": "contradicts_edge", "overlap": overlap},
                    )
                )
    except Exception as exc:
        logger.debug("contradicts-edge channel failed: %s", exc)

    hits.sort(key=lambda h: -h.score)
    return hits[:limit]


# ---------------------------------------------------------------------------
# Associative retrieval — follow graph edges across semantic boundaries
# ---------------------------------------------------------------------------


async def associative_search(
    anchor_item_id: str,
    anchor_type: str = "evidence",
    hops: int = 2,
    limit: int = 10,
) -> list[RetrievalHit]:
    """Traverse evidence_graph_edges from an anchor (1-2 hops).

    Finds material CONNECTED through intermediate concepts — the retrieval
    mode that crosses semantic boundaries (a database-caching finding can
    reach a human-memory observation through a shared intermediate claim).
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot associate: no DB pool")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH RECURSIVE walk(node, node_type, depth, path) AS (
                SELECT $1::text, $2::text, 0, ARRAY[$1::text]
                UNION ALL
                SELECT e.to_node, e.to_type, w.depth + 1,
                       w.path || e.to_node
                FROM evidence_graph_edges e
                JOIN walk w
                  ON e.from_node = w.node AND e.from_type = w.node_type
                WHERE w.depth < $3
                  AND NOT e.to_node = ANY(w.path)
            )
            SELECT DISTINCT w.node, w.node_type, w.depth
            FROM walk w
            WHERE w.depth > 0
            ORDER BY w.depth
            LIMIT $4
            """,
            anchor_item_id, anchor_type, hops, limit * 3,
        )

        hits: list[RetrievalHit] = []
        for r in rows:
            node, ntype, depth = r["node"], r["node_type"], r["depth"]
            content = ""
            case = None
            extra_type = None
            if ntype == "evidence":
                erow = await conn.fetchrow(
                    "SELECT case_id, content, evidence_type FROM evidence "
                    "WHERE evidence_id = $1", node,
                )
                if erow:
                    content, case = erow["content"], erow["case_id"]
                    extra_type = erow["evidence_type"]
            elif ntype == "claim":
                crow = await conn.fetchrow(
                    "SELECT case_id, statement, claim_type FROM claims "
                    "WHERE claim_id = $1", node,
                )
                if crow:
                    content, case = crow["statement"], crow["case_id"]
                    extra_type = crow["claim_type"]
            elif ntype == "hypothesis":
                hrow = await conn.fetchrow(
                    "SELECT case_id, statement FROM hypotheses "
                    "WHERE hypothesis_id = $1", node,
                )
                if hrow:
                    content, case = hrow["statement"], hrow["case_id"]
            if not content:
                continue
            hits.append(
                RetrievalHit(
                    item_type=ntype,
                    item_id=node,
                    case_id=case or "",
                    content=content,
                    score=1.0 / depth,
                    evidence_type=extra_type if ntype == "evidence" else None,
                    claim_type=extra_type if ntype == "claim" else None,
                    relation_to_anchor=f"{depth}-hop",
                    factors={"hops": depth},
                )
            )
        return hits[:limit]


async def find_cross_case_connections(
    case_id: str,
    limit: int = 5,
) -> list[dict]:
    """Find evidence in OTHER cases similar to this case's evidence.

    The cross-case connection finder: for each of this case's top evidence
    items, search every OTHER case for similar material. Returns connection
    dicts {from_evidence_id, to_case_id, to_evidence_id, score} — candidates
    for the endogenous 'possible connection between two apparently
    unrelated findings' source.
    """
    # Anchor selection: this case's most recent ACTIVE evidence items.
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot connect across cases: no pool")
    async with pool.acquire() as conn:
        anchors = await conn.fetch(
            "SELECT evidence_id, content FROM evidence "
            "WHERE case_id = $1 AND status = 'ACTIVE' "
            "ORDER BY recorded_at DESC LIMIT 3",
            case_id,
        )

    connections: list[dict] = []
    for anchor in anchors:
        terms = _content_tokens(anchor["content"])[:12]
        if not terms:
            continue
        # Progressive query relaxation: FTS plainto_tsquery ANDs every
        # term, so a 12-term query demands near-identical documents.
        # Relax 12 -> 6 -> 3 terms until the other case's similar (not
        # identical) evidence surfaces.
        # collect hits from EVERY width (not just the first that matches):
        # narrow queries surface near-duplicates, wider ones surface
        # thematically-related evidence in genuinely different cases.
        hits: list[RetrievalHit] = []
        for width in (12, 6, 3):
            query = " ".join(terms[:width])
            hits.extend(
                await search_evidence(
                    query, case_id=case_id, cross_case=True,
                    limit=limit * 3,
                )
            )
        for h in hits:
            connections.append(
                {
                    "from_evidence_id": anchor["evidence_id"],
                    "to_case_id": h.case_id,
                    "to_evidence_id": h.item_id,
                    "to_content": h.content[:200],
                    "score": round(h.score, 4),
                }
            )
    # best connection per (anchor, target CASE) — near-duplicate twin cases
    # (identical seeded content) must not crowd out genuinely different
    # related cases within the cap.
    deduped: dict[tuple[str, str], dict] = {}
    for c in connections:
        key = (c["from_evidence_id"], c["to_case_id"])
        if key not in deduped or c["score"] > deduped[key]["score"]:
            deduped[key] = c
    result = sorted(deduped.values(), key=lambda c: -c["score"])
    return result[:limit]
