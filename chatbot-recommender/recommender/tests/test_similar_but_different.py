"""Similar-but-different: the neighbour-category contract, the MMR
diversification effect, the seen-exclusion, and the fallback engine."""

from __future__ import annotations

import numpy as np
import pytest

from recommender.config import RecommenderConfig
from recommender.demo_data import build_demo_catalog
from recommender.embeddings import TfidfEmbedder
from recommender.engines.category_graph import NetworkxCategoryGraph
from recommender.engines.vector_store import NumpyVectorStore, QdrantVectorStore
from recommender.mechanisms.anti_repetition import AntiRepetition
from recommender.mechanisms.similar_but_different import (
    SimilarButDifferent,
    maximal_marginal_relevance,
)


def build_mechanism(vector_engine: str = "qdrant") -> SimilarButDifferent:
    catalog = build_demo_catalog()
    embedder = TfidfEmbedder()
    embedder.fit([item.text for item in catalog.items])
    vectors = embedder.encode([item.text for item in catalog.items])
    store = QdrantVectorStore() if vector_engine == "qdrant" else NumpyVectorStore()
    store.build(catalog.items, vectors)
    anti = AntiRepetition()
    return SimilarButDifferent(
        catalog, embedder, store, NetworkxCategoryGraph(), anti,
        RecommenderConfig(),
    )


@pytest.mark.parametrize("vector_engine", ["qdrant", "numpy"])
def test_recommendations_come_from_neighbour_categories(vector_engine: str) -> None:
    mechanism = build_mechanism(vector_engine)
    recommendations, diagnostics = mechanism.recommend(
        "I love oil painting and watercolor technique",
        focus_category="painting",
        k=5,
        turn=1,
    )
    assert len(recommendations) == 5
    assert diagnostics["focus_category"] == "painting"
    neighbor_set = {"sculpture", "photography", "architecture"}
    for recommendation in recommendations:
        assert recommendation.item.category in neighbor_set, (
            f"{recommendation.item.category} is not a painting neighbour"
        )
        assert "painting ->" in recommendation.reason


def test_focus_inference_from_message_text() -> None:
    mechanism = build_mechanism()
    category, confidence = mechanism.infer_focus(
        "I have been watching videos about oil painting and watercolor"
    )
    assert category == "painting"
    assert confidence > 0


def test_focus_inference_on_gibberish_is_honest() -> None:
    mechanism = build_mechanism()
    category, _confidence = mechanism.infer_focus("zzz qqq vvv")
    # gibberish has no nearest-neighbour signal; whatever comes back must
    # be a real category (or None) -- never a fabricated one
    assert category is None or category in mechanism.catalog.categories


def test_mmr_actually_diversifies() -> None:
    # construct candidates where the greedy top-3 are near-duplicates
    ids = ["a", "b", "c", "d"]
    relevance = {"a": 0.9, "b": 0.89, "c": 0.88, "d": 0.5}
    vectors = {
        "a": np.array([1.0, 0.0]),
        "b": np.array([0.99, 0.02]),   # nearly identical to a
        "c": np.array([0.98, 0.05]),   # nearly identical to a and b
        "d": np.array([0.0, 1.0]),     # orthogonal
    }
    greedy = ["a", "b", "c"]
    mmr = maximal_marginal_relevance(ids, relevance, vectors, k=3, lambda_const=0.5)
    # MMR must swap at least one near-duplicate for the orthogonal item
    assert "d" in mmr
    def avg_pairwise_similarity(selection: list[str]) -> float:
        total, count = 0.0, 0
        for first in range(len(selection)):
            for second in range(first + 1, len(selection)):
                u = vectors[selection[first]] / np.linalg.norm(vectors[selection[first]])
                v = vectors[selection[second]] / np.linalg.norm(vectors[selection[second]])
                total += float(np.dot(u, v))
                count += 1
        return total / count
    assert avg_pairwise_similarity(mmr) < avg_pairwise_similarity(greedy)


def test_seen_items_never_return() -> None:
    mechanism = build_mechanism()
    first, _ = mechanism.recommend(
        "oil painting", focus_category="painting", k=4, turn=1
    )
    for recommendation in first:
        mechanism.anti_repetition.mark_seen(
            recommendation.item.id,
            mechanism.vector_store.vector_of(recommendation.item.id),
            turn=1,
        )
    second, _ = mechanism.recommend(
        "oil painting", focus_category="painting", k=4, turn=2
    )
    first_ids = {recommendation.item.id for recommendation in first}
    second_ids = {recommendation.item.id for recommendation in second}
    assert not first_ids & second_ids


def test_same_category_fallback_only_when_neighbourhood_exhausted() -> None:
    mechanism = build_mechanism()
    # exhaust every reachable neighbour category (hop 1 AND hop 2)
    reachable = mechanism.graph.neighbors("painting", 2)
    assert reachable, "graph must know painting's neighbours"
    for item in mechanism.catalog.items_in_categories(reachable.keys()):
        mechanism.anti_repetition.mark_seen(item.id, None, turn=1)
    recommendations, _ = mechanism.recommend(
        "oil painting", focus_category="painting", k=2, turn=2
    )
    assert recommendations
    assert all(
        recommendation.item.category == "painting"
        for recommendation in recommendations
    )
    assert all(
        "same_category_fallback" in recommendation.mechanism
        for recommendation in recommendations
    )


def test_unknown_focus_returns_empty_honestly() -> None:
    mechanism = build_mechanism()
    recommendations, diagnostics = mechanism.recommend(
        "something", focus_category=None, k=3, turn=1
    )
    # with focus=None and no inferrable signal the mechanism refuses to
    # fabricate -- it either infers from text or returns nothing
    if recommendations:
        assert diagnostics["focus_category"] in mechanism.catalog.categories
    else:
        assert diagnostics["focus_category"] is None
