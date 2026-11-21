"""Mechanism 1: similar-but-different.

The user's spec: watching paintings should surface sculpture, not ten
more paintings. Concretely:

1. infer the user's focus category from their message (TF-IDF nearest
   neighbours' majority category);
2. retrieve semantically similar items -- but ONLY from neighbour
   categories (graph hop 1, then hop 2 to fill), never the focus
   category itself;
3. diversify the result set with MMR (maximal marginal relevance -- the
   same algorithm LangChain exposes, implemented directly so the whole
   LangChain dependency is not dragged in);
4. apply anti-repetition (hard exclusions + refractory rescoring).

The MMR step is why the five results are not five variants of the same
thing: each pick trades a little relevance for being different from
what is already in the set.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..catalog import Catalog, Item
from ..config import RecommenderConfig
from ..embeddings import Embedder
from ..engines.category_graph import CategoryGraph
from ..engines.vector_store import VectorStore
from .anti_repetition import AntiRepetition


@dataclass(frozen=True)
class Recommendation:
    item: Item
    score: float
    mechanism: str          # "similar_but_different" | "wildcard:<arm>" | ...
    reason: str             # human-readable, honest explanation


def maximal_marginal_relevance(
    candidate_ids: list[str],
    relevance: dict[str, float],
    vectors: dict[str, np.ndarray],
    k: int,
    lambda_const: float,
) -> list[str]:
    """Standard MMR selection (Carbonell & Goldstein 1998).

    iteratively pick argmax lambda*rel(i) - (1-lambda)*max_sim(i, selected).
    """
    selected: list[str] = []
    remaining = list(candidate_ids)

    def unit(vector: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vector) + 1e-9
        return vector / norm

    while remaining and len(selected) < k:
        best_id, best_score = None, -float("inf")
        for candidate_id in remaining:
            vector = vectors.get(candidate_id)
            if vector is None:
                continue
            diversity_term = 0.0
            for chosen_id in selected:
                chosen = vectors.get(chosen_id)
                if chosen is None:
                    continue
                similarity = float(np.dot(unit(vector), unit(chosen)))
                diversity_term = max(diversity_term, similarity)
            mmr = lambda_const * relevance.get(candidate_id, 0.0) - (
                1.0 - lambda_const
            ) * diversity_term
            if mmr > best_score:
                best_id, best_score = candidate_id, mmr
        if best_id is None:
            break
        selected.append(best_id)
        remaining.remove(best_id)
    return selected


class SimilarButDifferent:
    def __init__(
        self,
        catalog: Catalog,
        embedder: Embedder,
        vector_store: VectorStore,
        graph: CategoryGraph,
        anti_repetition: AntiRepetition,
        config: RecommenderConfig,
    ) -> None:
        self.catalog = catalog
        self.embedder = embedder
        self.vector_store = vector_store
        self.graph = graph
        self.anti_repetition = anti_repetition
        self.config = config

    # ── focus inference ─────────────────────────────────────────────────
    def infer_focus(self, message: str) -> tuple[str | None, float]:
        """Majority category of the message's nearest catalog items."""
        query = self.embedder.encode([message])[0]
        hits = self.vector_store.search(query, limit=9)
        if not hits:
            return None, 0.0
        votes: dict[str, list[float]] = {}
        for item_id, score in hits:
            category = self.catalog.by_id[item_id].category
            votes.setdefault(category, []).append(score)
        best_category, best_votes = None, -1.0
        for category, score_list in votes.items():
            total = sum(score_list)
            if total > best_votes:
                best_category, best_votes = category, total
        top_similarity = hits[0][1]
        confidence = min(1.0, best_votes) * (1.0 if top_similarity > 0.05 else 0.5)
        return best_category, confidence

    # ── the mechanism ───────────────────────────────────────────────────
    def recommend(
        self,
        interest_text: str,
        focus_category: str | None = None,
        k: int = 3,
        turn: int = 0,
        history_categories: list[str] | None = None,
    ) -> tuple[list[Recommendation], dict]:
        """Return (recommendations, diagnostics)."""
        if focus_category is None:
            focus_category, confidence = self.infer_focus(interest_text)
        else:
            confidence = 1.0
        diagnostics: dict = {
            "focus_category": focus_category,
            "focus_confidence": round(confidence, 3),
            "hops_used": [],
        }

        if focus_category is None:
            # Nothing recognizable: no honest neighbour hop exists.
            return [], diagnostics

        neighbors = self.graph.neighbors(focus_category, self.config.max_graph_hops)
        hop1 = sorted(cat for cat, hop in neighbors.items() if hop == 1)
        hop2 = sorted(cat for cat, hop in neighbors.items() if hop == 2)
        diagnostics["hops_used"] = hop1 or hop2

        query_vector = self.embedder.encode([interest_text])[0]
        seen_ids = [
            item_id
            for item_id in self.catalog.by_id
            if self.anti_repetition.is_seen(item_id)
        ]

        recommendations: list[Recommendation] = []
        explanation_note = "neighbour category"

        for categories, hop in ((hop1, 1), (hop2, 2)):
            if len(recommendations) >= k or not categories:
                continue
            hits = self.vector_store.search(
                query_vector,
                limit=self.config.neighbor_search_pool,
                category_whitelist=categories,
                exclude_ids=seen_ids,
            )
            if not hits:
                continue
            # soft refractory rescoring before MMR
            vectors = {
                item_id: self.vector_store.vector_of(item_id)
                for item_id, _score in hits
            }
            rescored = self.anti_repetition.penalize(hits, vectors, turn)
            relevance = dict(rescored)
            chosen = maximal_marginal_relevance(
                [item_id for item_id, _ in rescored],
                relevance,
                vectors,
                k - len(recommendations),
                self.config.mmr_lambda,
            )
            for item_id in chosen:
                item = self.catalog.by_id[item_id]
                recommendations.append(
                    Recommendation(
                        item=item,
                        score=float(relevance.get(item_id, 0.0)),
                        mechanism="similar_but_different",
                        reason=(
                            f"{explanation_note} {focus_category} -> {item.category} "
                            f"(hop {hop}, relevance {relevance.get(item_id, 0.0):.2f})"
                        ),
                    )
                )

        # last resort: same category (flagged -- this is the boring
        # mirror behaviour the mechanism exists to avoid, so it only
        # fires when the neighbourhood is exhausted)
        if len(recommendations) < k and self.config.allow_same_category_fallback:
            hits = self.vector_store.search(
                query_vector,
                limit=self.config.neighbor_search_pool,
                category_whitelist=[focus_category],
                exclude_ids=seen_ids,
            )
            for item_id, score in hits[: k - len(recommendations)]:
                item = self.catalog.by_id[item_id]
                recommendations.append(
                    Recommendation(
                        item=item,
                        score=float(score),
                        mechanism="similar_but_different:same_category_fallback",
                        reason=(
                            f"neighbourhood exhausted -- fell back to {item.category} "
                            f"(same category, relevance {score:.2f})"
                        ),
                    )
                )

        return recommendations[:k], diagnostics
