"""End-to-end chatbot scenario plus the FastAPI service."""

from __future__ import annotations

import pytest

from recommender import Chatbot, RecommenderConfig


@pytest.fixture(scope="module")
def bot() -> Chatbot:
    return Chatbot(RecommenderConfig())


def test_turn_one_similar_but_different(bot: Chatbot) -> None:
    reply = bot.chat("I have been watching videos about oil painting")
    assert reply.feed.focus_category == "painting"
    exploit = [
        recommendation
        for recommendation in reply.recommendations
        if not recommendation.mechanism.startswith("wildcard:")
    ]
    assert exploit, "no exploit recommendations on turn one"
    neighbours = {"sculpture", "photography", "architecture"}
    for recommendation in exploit:
        assert recommendation.item.category in neighbours
    assert reply.to_dict()["recommendations"]  # serializable


def test_session_never_repeats(bot: Chatbot) -> None:
    shown: set[str] = set()
    messages = [
        "oil painting",
        "watercolor technique",
        "marble sculpture",
        "street photography",
        "python coding",
        "bread baking",
        "metro maps",
        "stamp collecting",
    ]
    for message in messages:
        reply = bot.chat(message)
        ids = {recommendation.item.id for recommendation in reply.recommendations}
        assert not shown & ids
        shown.update(ids)


def test_feedback_is_accepted_and_stats_grow(bot: Chatbot) -> None:
    reply = bot.chat("documentary about lighthouses")
    first = reply.recommendations[0]
    accepted = bot.feedback(first.item.id, "liked")
    assert accepted
    # an id from a stale reply is rejected honestly (returns False)
    stale = Chatbot(RecommenderConfig())
    stale_reply = stale.chat("science")
    assert bot.feedback(stale_reply.recommendations[0].item.id, "liked") is False


def test_engine_report_names_real_engines(bot: Chatbot) -> None:
    report = bot.engine_report()
    assert report["vector"]["live"] and "qdrant" in report["vector"]["implementation"]
    assert report["bandit"]["live"] and "mab2rec" in report["bandit"]["implementation"]
    assert report["graph"]["live"] and "networkx" in report["graph"]["implementation"]
    assert report["embedder"]["live"] and report["embedder"]["dim"] > 0


def test_explicit_focus_overrides_inference() -> None:
    fresh = Chatbot(RecommenderConfig())
    reply = fresh.chat("something vague", focus_category="coding")
    assert reply.feed.focus_category == "coding"
    exploit = [
        recommendation
        for recommendation in reply.recommendations
        if not recommendation.mechanism.startswith("wildcard:")
    ]
    # coding's neighbours: science (hop 1), documentary (hop 1)
    assert {recommendation.item.category for recommendation in exploit} <= {
        "science", "documentary"
    }


def test_service_round_trip() -> None:
    from fastapi.testclient import TestClient

    from recommender.service import app

    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200

    engines = client.get("/engines")
    assert engines.status_code == 200
    assert engines.json()["bandit"]["live"] is True

    chat = client.post("/chat", json={"message": "oil painting and light"})
    assert chat.status_code == 200
    payload = chat.json()
    assert payload["focus_category"] == "painting"
    assert payload["recommendations"]

    first_id = payload["recommendations"][0]["id"]
    feedback = client.post(
        "/feedback", json={"item_id": first_id, "verdict": "liked"}
    )
    assert feedback.status_code == 200
    assert feedback.json()["accepted"] is True

    missing = client.post(
        "/feedback", json={"item_id": "item-9999", "verdict": "liked"}
    )
    assert missing.status_code == 404
