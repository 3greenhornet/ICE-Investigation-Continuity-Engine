"""Mechanism 2: wildcards -- the surprise channel.

Every turn the bandit picks one arm:

* ``none``             -- no wildcard this turn
* ``long_tail_old``    -- the old video with ~100 plays
* ``unknown_author``   -- the no-name creator
* ``orthogonal_topic`` -- a category graph-far from the user's focus
* ``pure_random``      -- uniform over the whole catalog

The pools are catalog predicates; WHICH pool (and whether a wildcard
fires at all) is the bandit's decision, and the bandit learns from
feedback: liked wildcards raise their arm, ignored ones lower it, and
if wildcards consistently flop the ``none`` arm wins and they get rare.
That is the "install a calendar app" bargain -- the exploration policy
is a real learning engine, not a hand-rolled random sprinkle.
"""

from __future__ import annotations

import numpy as np

from ..catalog import Catalog, Item
from ..config import RecommenderConfig
from ..engines.bandit import Bandit
from ..engines.category_graph import CategoryGraph
from .anti_repetition import AntiRepetition
from .similar_but_different import Recommendation


class Wildcards:
    def __init__(
        self,
        catalog: Catalog,
        graph: CategoryGraph,
        bandit: Bandit,
        anti_repetition: AntiRepetition,
        config: RecommenderConfig,
    ) -> None:
        self.catalog = catalog
        self.graph = graph
        self.bandit = bandit
        self.anti_repetition = anti_repetition
        self.config = config
        self._rng = np.random.default_rng(config.seed)
        # pool cache -- pools are catalog predicates, cheap to build once
        self._pools: dict[str, list[Item]] = {}
        self._last_context: np.ndarray | None = None
        self._last_arm: str | None = None

    # ── pools ───────────────────────────────────────────────────────────
    def pool_for(
        self, arm: str, focus_category: str | None, history_categories: list[str]
    ) -> list[Item]:
        if arm == "long_tail_old":
            return self.catalog.pool_long_tail_old(
                self.config.long_tail_max_plays, self.config.long_tail_min_age_days
            )
        if arm == "unknown_author":
            return self.catalog.pool_unknown_authors(
                self.config.unknown_author_max_followers
            )
        if arm == "orthogonal_topic":
            if focus_category:
                distances = self.graph.neighbors(
                    focus_category, max_hops=self.config.orthogonal_min_graph_distance + 2
                )
                distances = {**distances, focus_category: 0}
                # everything not reachable within orthogonal_min_graph_distance-1
                category_distance = {
                    category: distances.get(category, self.config.orthogonal_min_graph_distance + 5)
                    for category in self.catalog.categories
                }
            else:
                category_distance = {category: 5 for category in self.catalog.categories}
            return self.catalog.pool_orthogonal(focus_category, category_distance)
        if arm == "pure_random":
            return self.catalog.pool_pure_random()
        return []

    # ── the mechanism ───────────────────────────────────────────────────
    def recommend(
        self,
        context: np.ndarray,
        focus_category: str | None,
        history_categories: list[str],
        turn: int,
    ) -> tuple[Recommendation | None, str]:
        """Choose the arm via the bandit; return one wildcard or None."""
        arm = self.bandit.choose(context)
        self._last_arm = arm
        self._last_context = np.asarray(context, dtype=float).copy()
        if arm == "none":
            return None, arm

        pool = self.pool_for(arm, focus_category, history_categories)
        available = [
            item for item in pool if not self.anti_repetition.is_seen(item.id)
        ]
        if not available:
            # that corner of the catalog is exhausted -- say so honestly
            return None, arm

        if arm == "pure_random":
            item = available[int(self._rng.integers(0, len(available)))]
            reason = "pure random wildcard -- no targeting at all"
        else:
            # deterministic preference inside the pool: the MOST obscure /
            # most orthogonal first (stable ordering, no hidden randomness)
            def obscurity(item: Item) -> tuple[float, ...]:
                if arm == "long_tail_old":
                    return (-item.published_days_ago, item.play_count)
                if arm == "unknown_author":
                    return (item.author.followers, item.play_count)
                return (-item.play_count,)  # orthogonal: prefer non-viral

            available.sort(key=obscurity)
            item = available[0]
            if arm == "long_tail_old":
                reason = (
                    f"long-tail wildcard: {item.play_count} plays, "
                    f"{item.published_days_ago // 365} years old"
                )
            elif arm == "unknown_author":
                reason = (
                    f"unknown-author wildcard: {item.author.name} has "
                    f"{item.author.followers} followers"
                )
            else:
                reason = (
                    f"orthogonal-topic wildcard: {item.category} is graph-far "
                    f"from {focus_category or 'your focus'}"
                )

        return (
            Recommendation(
                item=item,
                score=0.0,
                mechanism=f"wildcard:{arm}",
                reason=reason,
            ),
            arm,
        )

    # ── feedback wiring ─────────────────────────────────────────────────
    @property
    def last_decision(self) -> tuple[str, np.ndarray | None]:
        return self._last_arm or "none", self._last_context

    def send_feedback(self, arm: str, reward: float, context: np.ndarray) -> None:
        self.bandit.update(arm, reward, context)

    def arm_stats(self) -> dict[str, dict[str, float]]:
        return self.bandit.arm_stats()

    def engine_name(self) -> str:
        return self.bandit.name()
