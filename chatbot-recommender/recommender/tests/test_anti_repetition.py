"""Anti-repetition: the Hari semantic-refractory port, unit-tested
against the original semantics (register, penalize, decay, prune, plus
the hard seen-set)."""

from __future__ import annotations

import numpy as np
import pytest

from recommender.mechanisms.anti_repetition import AntiRepetition


def unit(vector: list[float]) -> np.ndarray:
    array = np.array(vector, dtype=np.float32)
    return array / np.linalg.norm(array)


def test_hard_seen_set_never_returns_an_item() -> None:
    anti = AntiRepetition()
    anti.mark_seen("a", unit([1, 0]), turn=1)
    assert anti.is_seen("a")
    assert anti.filter_unseen(["a", "b", "c"]) == ["b", "c"]


def test_identical_embedding_is_suppressed_hard() -> None:
    anti = AntiRepetition(
        refractory_turns=4, similarity_threshold=0.55, max_penalty=0.65
    )
    embedding = unit([1, 0])
    anti.mark_seen("shown", embedding, turn=1)
    # the very next turn: same direction -> maximum suppression
    multiplier = anti.penalty_multiplier("other", embedding, turn=2)
    assert multiplier == pytest.approx(1.0 - 0.65)  # at the penalty floor


def test_suppression_decays_over_the_window() -> None:
    anti = AntiRepetition(
        refractory_turns=4, similarity_threshold=0.55, max_penalty=0.65
    )
    embedding = unit([1, 0])
    anti.mark_seen("shown", embedding, turn=1)
    turn2 = anti.penalty_multiplier("other", embedding, turn=2)
    turn3 = anti.penalty_multiplier("other", embedding, turn=3)
    turn5 = anti.penalty_multiplier("other", embedding, turn=5)
    assert turn2 < turn3 < turn5
    # after the window: fully recovered
    assert anti.penalty_multiplier("other", embedding, turn=6) == pytest.approx(1.0)


def test_dissimilar_items_are_not_punished() -> None:
    anti = AntiRepetition(
        refractory_turns=4, similarity_threshold=0.55, max_penalty=0.65
    )
    anti.mark_seen("shown", unit([1, 0]), turn=1)
    orthogonal = unit([0, 1])
    assert anti.penalty_multiplier("other", orthogonal, turn=2) == pytest.approx(1.0)


def test_prune_drops_expired_entries() -> None:
    anti = AntiRepetition(refractory_turns=2)
    anti.mark_seen("old", unit([1, 0]), turn=1)
    anti.prune(current_turn=10)
    snapshot = anti.snapshot()
    assert snapshot["suppressed_count"] == 0
    # still seen (hard layer), but no longer suppressed (soft layer)
    assert anti.is_seen("old")
    assert anti.penalty_multiplier("old-neighbor", unit([1, 0]), turn=10) == 1.0


def test_seen_memory_cap_evicts_oldest() -> None:
    anti = AntiRepetition(seen_memory=3)
    for index in range(5):
        anti.mark_seen(f"item-{index}", unit([1, index]), turn=1)
    assert anti.is_seen("item-4") and anti.is_seen("item-3")
    assert not anti.is_seen("item-0") and not anti.is_seen("item-1")


def test_penalize_rescores_a_ranked_list() -> None:
    anti = AntiRepetition(
        refractory_turns=4, similarity_threshold=0.55, max_penalty=0.65
    )
    same = unit([1, 0])
    different = unit([0, 1])
    anti.mark_seen("shown", same, turn=1)
    rescored = anti.penalize(
        [("same-ish", 0.9), ("different", 0.5)],
        {"same-ish": same, "different": different},
        turn=2,
    )
    scores = dict(rescored)
    assert scores["same-ish"] < 0.9 * 0.36  # heavy suppression
    assert scores["different"] == pytest.approx(0.5)  # untouched
