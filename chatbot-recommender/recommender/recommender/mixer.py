"""The mixer: composes the feed from the mechanisms and owns the loop.

Per turn:
* the focus category is inferred once from the user's message;
* the bandit decides whether a wildcard fires (and which pool);
* the remaining slots go to similar-but-different around the focus;
* anti-repetition applies globally (nothing shown ever returns; similar
  items are refractorily suppressed);
* feedback on a wildcard slot trains the bandit (that is how the
  exploration policy adapts to THIS user).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .catalog import Catalog
from .config import RecommenderConfig
from .embeddings import Embedder
from .engines.bandit import Bandit
from .engines.category_graph import CategoryGraph
from .engines.vector_store import VectorStore
from .mechanisms.anti_repetition import AntiRepetition
from .mechanisms.similar_but_different import Recommendation, SimilarButDifferent
from .mechanisms.wildcards import Wildcards


@dataclass
class Feed:
    turn: int
    focus_category: str | None
    recommendations: list[Recommendation] = field(default_factory=list)
    wildcard_arm: str | None = None
    breakdown: dict = field(default_factory=dict)


@dataclass
class SessionState:
    turn: int = 0
    history_categories: list[str] = field(default_factory=list)
    wildcard_feedback: list[tuple[str, float]] = field(default_factory=list)
    exploit_engaged: int = 0
    exploit_shown: int = 0
    turns_since_wildcard: int = 99

    def context_vector(self) -> np.ndarray:
        """The bandit's view of the user. Four honest dimensions, all [0,1]."""
        if self.wildcard_feedback:
            wildcard_success = float(
                np.mean([reward for _arm, reward in self.wildcard_feedback])
            )
        else:
            wildcard_success = 0.5
        category_counts: dict[str, int] = {}
        for category in self.history_categories:
            category_counts[category] = category_counts.get(category, 0) + 1
        total = max(1, len(self.history_categories))
        proportions = np.array(list(category_counts.values()), dtype=float) / total
        entropy = float(-(proportions * np.log(proportions + 1e-9)).sum())
        max_entropy = float(np.log(max(2, len(category_counts) or 2)))
        history_diversity = entropy / max_entropy if max_entropy > 0 else 0.0
        engage_rate = (
            self.exploit_engaged / self.exploit_shown if self.exploit_shown else 0.5
        )
        return np.array(
            [
                wildcard_success,
                min(1.0, self.turns_since_wildcard / 10.0),
                history_diversity,
                engage_rate,
            ],
            dtype=float,
        )


class FeedMixer:
    def __init__(
        self,
        catalog: Catalog,
        embedder: Embedder,
        vector_store: VectorStore,
        graph: CategoryGraph,
        bandit: Bandit,
        config: RecommenderConfig,
    ) -> None:
        self.config = config
        self.catalog = catalog
        self.embedder = embedder
        self.vector_store = vector_store
        self.anti_repetition = AntiRepetition(
            refractory_turns=config.refractory_turns,
            similarity_threshold=config.refractory_similarity,
            max_penalty=config.refractory_max_penalty,
            seen_memory=config.seen_memory,
        )
        self.similar_but_different = SimilarButDifferent(
            catalog, embedder, vector_store, graph, self.anti_repetition, config
        )
        self.wildcards = Wildcards(
            catalog, graph, bandit, self.anti_repetition, config
        )
        self.state = SessionState()

    def _embedding_of(self, item_id: str) -> np.ndarray | None:
        return self.vector_store.vector_of(item_id)

    # ── composition ─────────────────────────────────────────────────────
    def compose(
        self,
        interest_text: str,
        focus_category: str | None = None,
        k: int | None = None,
    ) -> Feed:
        k = k or self.config.feed_size
        self.state.turn += 1
        turn = self.state.turn

        # 1. focus: explicit if given, otherwise inferred once, honestly
        focus_diagnostics: dict = {}
        if focus_category is None:
            focus_category, confidence = self.similar_but_different.infer_focus(
                interest_text
            )
            focus_diagnostics["inferred"] = True
            focus_diagnostics["confidence"] = round(confidence, 3)
        focus_diagnostics["focus_category"] = focus_category

        # 2. wildcard decision (needs the focus for the orthogonal pool)
        context = self.state.context_vector()
        wildcard, arm = self.wildcards.recommend(
            context=context,
            focus_category=focus_category,
            history_categories=self.state.history_categories,
            turn=turn,
        )
        wildcard_slots = 1 if wildcard is not None else 0

        # 3. exploit slots: similar-but-different around the focus
        recommendations, hop_diagnostics = self.similar_but_different.recommend(
            interest_text,
            focus_category=focus_category,
            k=k - wildcard_slots,
            turn=turn,
            history_categories=self.state.history_categories,
        )
        focus_diagnostics.update(hop_diagnostics)

        feed = Feed(
            turn=turn,
            focus_category=focus_category,
            recommendations=[],
            wildcard_arm=arm if wildcard is not None else None,
            breakdown={
                "similar_but_different": len(recommendations),
                "wildcard": wildcard_slots,
                "arm_chosen": arm,
                "focus_diagnostics": focus_diagnostics,
            },
        )

        # 4. register everything as seen (with real embeddings for the
        #    refractory layer) -- impressions count, clicks are separate
        for recommendation in recommendations:
            self.anti_repetition.mark_seen(
                recommendation.item.id,
                self._embedding_of(recommendation.item.id),
                turn,
            )
            feed.recommendations.append(recommendation)

        if wildcard is not None:
            self.anti_repetition.mark_seen(
                wildcard.item.id, self._embedding_of(wildcard.item.id), turn
            )
            feed.recommendations.append(wildcard)
            self.state.turns_since_wildcard = 0
        else:
            self.state.turns_since_wildcard += 1

        if focus_category:
            self.state.history_categories.append(focus_category)
        self.anti_repetition.prune(turn)
        return feed

    # ── feedback ────────────────────────────────────────────────────────
    def register_feedback(
        self, recommendation: Recommendation, verdict: str
    ) -> None:
        """verdict: 'liked' | 'ignored' | 'disliked'."""
        reward_map = {"liked": 1.0, "ignored": 0.0, "disliked": -0.5}
        reward = reward_map.get(verdict, 0.0)

        if recommendation.mechanism.startswith("wildcard:"):
            arm = recommendation.mechanism.split(":", 1)[1]
            context = self.state.context_vector()
            self.wildcards.send_feedback(arm, reward, context)
            self.state.wildcard_feedback.append((arm, reward))
        else:
            self.state.exploit_shown += 1
            if verdict == "liked":
                self.state.exploit_engaged += 1
            # "none" competes: it earns the exploit engagement, so the
            # bandit can honestly learn that wildcards are not paying off
            if self.state.exploit_shown >= 3:
                arm, context = self.wildcards.last_decision
                if arm == "none":
                    self.wildcards.send_feedback(
                        "none", reward if verdict != "disliked" else 0.0, context
                    )

    def stats(self) -> dict:
        return {
            "turn": self.state.turn,
            "seen_items": self.anti_repetition.snapshot()["seen_count"],
            "history_categories": self.state.history_categories[-10:],
            "wildcard_arm_stats": self.wildcards.arm_stats(),
            "bandit_engine": self.wildcards.engine_name(),
        }
