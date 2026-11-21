"""Mixer: composition, the no-repeat guarantee across turns, and the
feedback loop that trains the bandit."""

from __future__ import annotations

import pytest

from recommender.config import RecommenderConfig
from recommender.demo_data import build_demo_catalog
from recommender.embeddings import TfidfEmbedder
from recommender.engines.bandit import EpsilonGreedyBandit
from recommender.engines.category_graph import NetworkxCategoryGraph
from recommender.engines.vector_store import NumpyVectorStore
from recommender.mixer import FeedMixer


@pytest.fixture()
def mixer() -> FeedMixer:
    catalog = build_demo_catalog()
    embedder = TfidfEmbedder()
    embedder.fit([item.text for item in catalog.items])
    vectors = embedder.encode([item.text for item in catalog.items])
    store = NumpyVectorStore()  # fast fallback engine for mixer tests
    store.build(catalog.items, vectors)
    return FeedMixer(
        catalog, embedder, store, NetworkxCategoryGraph(),
        EpsilonGreedyBandit(RecommenderConfig().wildcard_arms, seed=11),
        RecommenderConfig(),
    )


def test_feed_composition_and_slot_accounting(mixer: FeedMixer) -> None:
    feed = mixer.compose("oil painting and watercolor", k=5)
    assert len(feed.recommendations) == 5
    breakdown = feed.breakdown
    assert breakdown["similar_but_different"] + breakdown["wildcard"] == 5
    assert breakdown["wildcard"] in (0, 1)
    if feed.wildcard_arm:
        assert any(
            recommendation.mechanism.startswith("wildcard:")
            for recommendation in feed.recommendations
        )


def test_no_item_ever_repeats_across_many_turns(mixer: FeedMixer) -> None:
    shown: set[str] = set()
    messages = [
        "oil painting technique",
        "sculpture and marble carving",
        "street photography",
        "brutalist architecture",
        "python coding tutorial",
        "ramen and bread baking",
        "lighthouse documentaries",
        "metro map design",
        "stamp collecting history",
        "soil science",
    ]
    for turn, message in enumerate(messages, start=1):
        feed = mixer.compose(message, k=4)
        ids = [recommendation.item.id for recommendation in feed.recommendations]
        assert len(ids) == len(set(ids)), f"within-turn duplicate at turn {turn}"
        assert not shown & set(ids), f"cross-turn repeat at turn {turn}"
        shown.update(ids)
    assert mixer.stats()["seen_items"] == len(shown)


def test_wildcard_fires_within_a_session(mixer: FeedMixer) -> None:
    wildcard_seen = False
    for _turn in range(25):
        feed = mixer.compose("painting", k=4)
        if feed.wildcard_arm is not None:
            wildcard_seen = True
            break
    assert wildcard_seen, "no wildcard fired in 25 turns"


def test_feedback_trains_the_bandit(mixer: FeedMixer) -> None:
    # drive enough turns that the "none" arm earns exploit feedback
    for _ in range(6):
        feed = mixer.compose("painting", k=3)
        for recommendation in feed.recommendations:
            if not recommendation.mechanism.startswith("wildcard:"):
                mixer.register_feedback(recommendation, "liked")
    stats = mixer.stats()
    arm_stats = stats["wildcard_arm_stats"]
    pulls = [entry["pulls"] for entry in arm_stats.values()]
    assert sum(pulls) > 0, "no feedback reached the bandit"


def test_wildcard_feedback_updates_its_arm(mixer: FeedMixer) -> None:
    # find or force a wildcard turn
    for _turn in range(30):
        feed = mixer.compose("painting", k=3)
        wildcard = next(
            (r for r in feed.recommendations if r.mechanism.startswith("wildcard:")),
            None,
        )
        if wildcard is not None:
            before = mixer.stats()["wildcard_arm_stats"][feed.wildcard_arm]["pulls"]
            mixer.register_feedback(wildcard, "liked")
            after = mixer.stats()["wildcard_arm_stats"][feed.wildcard_arm]["pulls"]
            assert after == before + 1
            return
    pytest.fail("no wildcard appeared in 30 turns")


def test_context_vector_stays_in_unit_range() -> None:
    from recommender.mixer import SessionState

    state = SessionState()
    state.history_categories = ["painting"] * 5 + ["sculpture"]
    state.wildcard_feedback = [("pure_random", 1.0), ("none", 0.0)]
    state.exploit_shown, state.exploit_engaged = 10, 4
    context = state.context_vector()
    assert context.shape == (4,)
    assert all(0.0 <= value <= 1.0 for value in context)
