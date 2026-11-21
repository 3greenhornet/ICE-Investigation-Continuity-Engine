"""Wildcards: pool predicates on the chosen arm, bandit learning on
feedback, and every bandit engine adapter round-tripping."""

from __future__ import annotations

import numpy as np
import pytest

from recommender.config import RecommenderConfig
from recommender.demo_data import build_demo_catalog
from recommender.engines.bandit import (
    EpsilonGreedyBandit,
    Mab2RecBandit,
    VowpalWabbitBandit,
)
from recommender.engines.category_graph import NetworkxCategoryGraph
from recommender.mechanisms.anti_repetition import AntiRepetition
from recommender.mechanisms.wildcards import Wildcards

CONFIG = RecommenderConfig()
CONTEXT = np.array([0.5, 0.8, 0.4, 0.5])


def build_wildcards(bandit_engine: str = "mab2rec") -> Wildcards:
    catalog = build_demo_catalog()
    arms = CONFIG.wildcard_arms
    if bandit_engine == "mab2rec":
        bandit = Mab2RecBandit(arms, seed=CONFIG.seed)
    elif bandit_engine == "vowpal_wabbit":
        bandit = VowpalWabbitBandit(arms, seed=CONFIG.seed)
    else:
        bandit = EpsilonGreedyBandit(arms, seed=CONFIG.seed)
    return Wildcards(
        catalog, NetworkxCategoryGraph(), bandit, AntiRepetition(), CONFIG
    )


@pytest.mark.parametrize(
    "arm",
    ["long_tail_old", "unknown_author", "orthogonal_topic", "pure_random"],
)
def test_each_arm_returns_items_matching_its_pool(arm: str) -> None:
    wildcards = build_wildcards()
    recommendation, chosen = wildcards.recommend(
        context=CONTEXT, focus_category="painting",
        history_categories=["painting"], turn=1,
    )
    # force the arm by calling the pool directly for the assertion part
    pool = wildcards.pool_for(arm, "painting", ["painting"])
    assert pool, f"pool for {arm} must not be empty"
    if arm == "long_tail_old":
        assert all(item.is_long_tail_old for item in pool)
    if arm == "unknown_author":
        assert all(item.author.followers <= CONFIG.unknown_author_max_followers
                   for item in pool)
    if arm == "orthogonal_topic":
        assert all(item.category not in {"painting", "sculpture", "photography",
                                         "architecture"} for item in pool)


def test_recommend_returns_pool_member_for_the_chosen_arm() -> None:
    wildcards = build_wildcards()
    for _attempt in range(20):
        recommendation, arm = wildcards.recommend(
            context=CONTEXT, focus_category="painting",
            history_categories=["painting"], turn=1,
        )
        if arm != "none" and recommendation is not None:
            pool_ids = {
                item.id for item in wildcards.pool_for(arm, "painting", ["painting"])
            }
            assert recommendation.item.id in pool_ids
            assert recommendation.mechanism == f"wildcard:{arm}"
            return
    pytest.fail("no wildcard fired in 20 attempts -- bandit is broken")


def test_exhausted_pool_is_admitted_honestly() -> None:
    wildcards = build_wildcards()
    # exhaust the long-tail pool
    for item in wildcards.catalog.pool_long_tail_old(800, 365):
        wildcards.anti_repetition.mark_seen(item.id, None, turn=1)
    # keep drawing until long_tail_old would be chosen; it must admit
    # exhaustion rather than fabricate
    for _attempt in range(50):
        recommendation, arm = wildcards.recommend(
            context=CONTEXT, focus_category="painting",
            history_categories=["painting"], turn=2,
        )
        if arm == "long_tail_old":
            assert recommendation is None  # honest exhaustion
            return


@pytest.mark.parametrize("bandit_engine", ["mab2rec", "epsilon", "vowpal_wabbit"])
def test_bandit_learns_from_rewards(bandit_engine: str) -> None:
    """Warm every arm neutrally; reward one arm; its share must rise."""
    wildcards = build_wildcards(bandit_engine)
    arms = CONFIG.wildcard_arms
    target = "long_tail_old"

    # neutral warm-up: one pull per arm, same reward everywhere -> no
    # signal yet, so the "before" share is near-uniform
    for arm in arms:
        wildcards.send_feedback(arm, 0.5, CONTEXT)

    before = _selection_share(wildcards, target, trials=60)
    # hammer the target arm with rewards
    for _ in range(30):
        wildcards.send_feedback(target, 1.0, CONTEXT)
    after = _selection_share(wildcards, target, trials=60)
    assert after > before, (
        f"{bandit_engine}: expected share to rise (before={before:.2f}, after={after:.2f})"
    )


def _selection_share(wildcards: Wildcards, arm: str, trials: int) -> float:
    hits = 0
    for _ in range(trials):
        _recommendation, chosen = wildcards.recommend(
            context=CONTEXT, focus_category="painting",
            history_categories=["painting"], turn=3,
        )
        if chosen == arm:
            hits += 1
    return hits / trials


def test_none_arm_can_win_when_wildcards_flop() -> None:
    wildcards = build_wildcards("epsilon")
    arms = CONFIG.wildcard_arms
    for arm in arms:
        wildcards.send_feedback(arm, 0.0 if arm != "none" else 1.0, CONTEXT)
    share = _selection_share(wildcards, "none", trials=40)
    assert share > 0.5, "with all wildcard arms failing, 'none' should dominate"


def test_arm_stats_are_reported() -> None:
    wildcards = build_wildcards()
    wildcards.send_feedback("pure_random", 1.0, CONTEXT)
    stats = wildcards.arm_stats()
    assert stats["pure_random"]["pulls"] == 1.0
    assert stats["pure_random"]["avg_reward"] == 1.0


def test_vowpal_wabbit_adapter_round_trip() -> None:
    bandit = VowpalWabbitBandit(("a", "b"), seed=3)
    context = np.array([0.2, 0.9])
    arm = bandit.choose(context)
    assert arm in ("a", "b")
    bandit.update(arm, 1.0, context)
    stats = bandit.arm_stats()
    assert stats[arm]["pulls"] == 1.0


def test_gorse_adapter_requires_endpoint() -> None:
    from recommender.engines.bandit import GorseExploreClient

    with pytest.raises(RuntimeError, match="GORSE_ENDPOINT"):
        GorseExploreClient(("none",))
