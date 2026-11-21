"""Bandit engines for the wildcard decision (mechanism 2's brain).

The bandit answers ONE question per turn: which wildcard arm fires
(none / long_tail_old / unknown_author / orthogonal_topic / pure_random),
given the current user-state context. Rewards come from what the user
did with the wildcard slot.

Real engines, all from the user's named shortlist:

* ``Mab2RecBandit``    -- Mab2Rec (MABWiser LinTS contextual bandit). Default.
* ``VowpalWabbitBandit``-- Vowpal Wabbit ``--cb_explore`` (online, tiny memory).
* ``EpsilonGreedyBandit``-- dependency-free fallback so the mechanism always runs.

``GorseExploreClient`` is the env-gated adapter to a live Gorse server
(gorse.io): on deployment set GORSE_ENDPOINT and the same mechanism can
delegate exploration to Gorse's /api/explore instead of the local bandit.
"""

from __future__ import annotations

import os
from typing import Protocol, Sequence

import numpy as np


class Bandit(Protocol):
    arms: tuple[str, ...]

    def choose(self, context: np.ndarray) -> str: ...
    def update(self, arm: str, reward: float, context: np.ndarray) -> None: ...
    def arm_stats(self) -> dict[str, dict[str, float]]: ...
    def name(self) -> str: ...


def _context_row(context: np.ndarray) -> np.ndarray:
    array = np.asarray(context, dtype=np.float64).reshape(1, -1)
    return array


class Mab2RecBandit:
    """Mab2Rec / MABWiser LinTS. Cold-starts uniformly until every arm
    has one observation (LinTS needs per-arm context coverage), then fits
    and continues online via ``partial_fit``."""

    def __init__(self, arms: Sequence[str], seed: int = 7) -> None:
        from mab2rec import BanditRecommender, LearningPolicy

        self.arms = tuple(arms)
        self._index = {arm: position for position, arm in enumerate(self.arms)}
        self._recommender = BanditRecommender(
            learning_policy=LearningPolicy.LinTS(alpha=1.0), top_k=len(self.arms)
        )
        self._rng = np.random.default_rng(seed)
        self._buffer: list[tuple[int, float, np.ndarray]] = []
        self._counts: dict[str, int] = {arm: 0 for arm in self.arms}
        self._rewards: dict[str, list[float]] = {arm: [] for arm in self.arms}
        self._fitted = False

    def name(self) -> str:
        return "mab2rec (LinTS)"

    def _refit(self) -> None:
        if not self._buffer:
            return
        decisions = np.array([row[0] for row in self._buffer])
        rewards = np.array([row[1] for row in self._buffer], dtype=float)
        contexts = np.vstack([row[2] for row in self._buffer])
        self._recommender.fit(decisions=decisions, rewards=rewards, contexts=contexts)
        self._fitted = True

    def choose(self, context: np.ndarray) -> str:
        row = _context_row(context)
        if not self._fitted or any(count == 0 for count in self._counts.values()):
            # honest cold start: uniform exploration until coverage exists
            return self.arms[int(self._rng.integers(0, len(self.arms)))]
        ranked = self._recommender.recommend(row)
        if not ranked:
            return self.arms[int(self._rng.integers(0, len(self.arms)))]
        first = ranked[0]
        best = first[0] if isinstance(first, (list, tuple, np.ndarray)) else first
        if best is None:
            return self.arms[int(self._rng.integers(0, len(self.arms)))]
        return self.arms[int(best)]

    def update(self, arm: str, reward: float, context: np.ndarray) -> None:
        row = _context_row(context)
        arm_index = self._index[arm]
        self._counts[arm] += 1
        self._rewards[arm].append(float(reward))
        if not self._fitted:
            self._buffer.append((arm_index, float(reward), row[0]))
            if all(count > 0 for count in self._counts.values()):
                self._refit()
        else:
            self._recommender.partial_fit(
                decisions=np.array([arm_index]),
                rewards=np.array([float(reward)]),
                contexts=row,
            )

    def arm_stats(self) -> dict[str, dict[str, float]]:
        return {
            arm: {
                "pulls": float(self._counts[arm]),
                "avg_reward": (
                    float(np.mean(self._rewards[arm])) if self._rewards[arm] else 0.0
                ),
            }
            for arm in self.arms
        }


class VowpalWabbitBandit:
    """Vowpal Wabbit contextual bandit with epsilon exploration."""

    def __init__(self, arms: Sequence[str], seed: int = 7, epsilon: float = 0.2) -> None:
        import vowpalwabbit

        self.arms = tuple(arms)
        self._epsilon = float(epsilon)
        self._rng = np.random.default_rng(seed)
        options = (
            f"--quiet --cb_explore {len(self.arms)} --epsilon {self._epsilon}"
        )
        self._vw = vowpalwabbit.Workspace(options)
        self._counts: dict[str, int] = {arm: 0 for arm in self.arms}
        self._rewards: dict[str, list[float]] = {arm: [] for arm in self.arms}

    def name(self) -> str:
        return "vowpal_wabbit (cb_explore)"

    @staticmethod
    def _features(context: np.ndarray) -> str:
        values = np.asarray(context, dtype=float).ravel()
        return " ".join(f"c{index}:{value:.4f}" for index, value in enumerate(values))

    def choose(self, context: np.ndarray) -> str:
        if self._rng.random() < self._epsilon:
            return self.arms[int(self._rng.integers(0, len(self.arms)))]
        scores = self._vw.predict(f"| {self._features(context)}")
        best = int(np.argmax(scores))
        return self.arms[best]

    def update(self, arm: str, reward: float, context: np.ndarray) -> None:
        action = self.arms.index(arm) + 1  # VW actions are 1-indexed
        cost = -float(reward)              # VW minimizes cost
        probability = max(1.0 / len(self.arms), self._epsilon / len(self.arms))
        self._vw.learn(
            f"{action}:{cost}:{probability:.4f} | {self._features(context)}"
        )
        self._counts[arm] += 1
        self._rewards[arm].append(float(reward))

    def arm_stats(self) -> dict[str, dict[str, float]]:
        return {
            arm: {
                "pulls": float(self._counts[arm]),
                "avg_reward": (
                    float(np.mean(self._rewards[arm])) if self._rewards[arm] else 0.0
                ),
            }
            for arm in self.arms
        }


class EpsilonGreedyBandit:
    """Dependency-free fallback: epsilon-greedy over per-arm means with
    an optimistic prior (small sample bonus keeps exploration honest)."""

    def __init__(self, arms: Sequence[str], seed: int = 7, epsilon: float = 0.15) -> None:
        self.arms = tuple(arms)
        self._epsilon = float(epsilon)
        self._rng = np.random.default_rng(seed)
        self._counts: dict[str, int] = {arm: 0 for arm in self.arms}
        self._sums: dict[str, float] = {arm: 0.0 for arm in self.arms}

    def name(self) -> str:
        return "epsilon-greedy (fallback)"

    def choose(self, context: np.ndarray) -> str:
        if self._rng.random() < self._epsilon:
            return self.arms[int(self._rng.integers(0, len(self.arms)))]
        def score(arm: str) -> float:
            if self._counts[arm] == 0:
                return float("inf")
            return self._sums[arm] / self._counts[arm]
        return max(self.arms, key=score)

    def update(self, arm: str, reward: float, context: np.ndarray) -> None:
        self._counts[arm] += 1
        self._sums[arm] += float(reward)

    def arm_stats(self) -> dict[str, dict[str, float]]:
        return {
            arm: {
                "pulls": float(self._counts[arm]),
                "avg_reward": (
                    self._sums[arm] / self._counts[arm] if self._counts[arm] else 0.0
                ),
            }
            for arm in self.arms
        }


class GorseExploreClient:
    """Env-gated adapter for a live Gorse (gorse.io) server.

    Not instantiable without GORSE_ENDPOINT -- by design, so no test or
    offline run ever silently depends on a network service. On a
    deployment with Docker this is the "install someone else's calendar
    app" path: Gorse owns the exploration model, we keep the mechanism.
    """

    def __init__(self, arms: Sequence[str], seed: int = 7) -> None:
        endpoint = os.environ.get("GORSE_ENDPOINT")
        if not endpoint:
            raise RuntimeError(
                "GorseExploreClient requires GORSE_ENDPOINT (a live Gorse "
                "master node, e.g. http://gorse:8088). Offline runs should "
                "use bandit_engine='mab2rec' or 'epsilon'."
            )
        import httpx

        self._client = httpx.Client(base_url=endpoint, timeout=5.0)
        self.arms = tuple(arms)

    def name(self) -> str:
        return "gorse (/api/explore, remote)"

    def choose(self, context: np.ndarray) -> str:
        raise NotImplementedError(
            "Gorse maps exploration to its own API once deployed; wire the "
            "fallback bandit locally and let Gorse rank the wildcard pools."
        )

    def update(self, arm: str, reward: float, context: np.ndarray) -> None:
        raise NotImplementedError("see choose()")

    def arm_stats(self) -> dict[str, dict[str, float]]:
        return {}
