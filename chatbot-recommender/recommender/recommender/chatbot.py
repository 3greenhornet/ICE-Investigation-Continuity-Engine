"""The chatbot facade: user message in, honest recommendations out.

Framework-free (usable from a REPL, a queue consumer, or the FastAPI
service). Every reply carries the mechanism breakdown so the bot can
always answer "why did you show me this?" truthfully.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import Catalog
from .config import RecommenderConfig
from .demo_data import build_demo_catalog
from .embeddings import Embedder, TfidfEmbedder
from .engines import (
    engine_report,
    resolve_bandit_engine,
    resolve_graph_engine,
    resolve_vector_engine,
)
from .mechanisms.similar_but_different import Recommendation
from .mixer import Feed, FeedMixer


@dataclass
class Reply:
    text: str
    feed: Feed
    recommendations: list[Recommendation] = field(default_factory=list)
    engines: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "turn": self.feed.turn,
            "focus_category": self.feed.focus_category,
            "wildcard_arm": self.feed.wildcard_arm,
            "breakdown": self.feed.breakdown,
            "recommendations": [
                {
                    "id": recommendation.item.id,
                    "title": recommendation.item.title,
                    "category": recommendation.item.category,
                    "author": recommendation.item.author.name,
                    "author_followers": recommendation.item.author.followers,
                    "play_count": recommendation.item.play_count,
                    "age_years": recommendation.item.published_days_ago // 365,
                    "kind": recommendation.item.kind,
                    "mechanism": recommendation.mechanism,
                    "reason": recommendation.reason,
                    "score": round(recommendation.score, 4),
                }
                for recommendation in self.recommendations
            ],
        }


class Chatbot:
    def __init__(self, config: RecommenderConfig | None = None,
                 catalog: Catalog | None = None,
                 embedder: Embedder | None = None) -> None:
        self.config = config or RecommenderConfig()
        self.catalog = catalog or build_demo_catalog()
        self.embedder = embedder or TfidfEmbedder()

        texts = [item.text for item in self.catalog.items]
        self.embedder.fit(texts)
        vectors = self.embedder.encode(texts)

        self.vector_store = resolve_vector_engine(self.config.vector_engine)
        self.vector_store.build(self.catalog.items, vectors)
        self.graph = resolve_graph_engine(self.config.graph_engine)
        self.bandit = resolve_bandit_engine(
            self.config.bandit_engine, self.config.wildcard_arms, self.config.seed
        )
        self.mixer = FeedMixer(
            self.catalog, self.embedder, self.vector_store, self.graph,
            self.bandit, self.config,
        )
        self._last_feed: Feed | None = None
        self._last_recommendations: dict[str, Recommendation] = {}

    # ── conversation ────────────────────────────────────────────────────
    def chat(self, message: str, focus_category: str | None = None,
             k: int | None = None) -> Reply:
        feed = self.mixer.compose(message, focus_category=focus_category, k=k)
        self._last_feed = feed
        self._last_recommendations = {
            recommendation.item.id: recommendation
            for recommendation in feed.recommendations
        }
        text = self._render(feed)
        return Reply(
            text=text,
            feed=feed,
            recommendations=list(feed.recommendations),
            engines={},
        )

    def feedback(self, item_id: str, verdict: str) -> bool:
        """verdict: 'liked' | 'ignored' | 'disliked'."""
        recommendation = self._last_recommendations.get(item_id)
        if recommendation is None:
            return False
        self.mixer.register_feedback(recommendation, verdict)
        return True

    # ── rendering ───────────────────────────────────────────────────────
    @staticmethod
    def _describe_item(recommendation: Recommendation) -> str:
        item = recommendation.item
        age_years = item.published_days_ago // 365
        plays = f"{item.play_count:,} plays"
        followers = f"{item.author.followers:,} followers"
        return (
            f"- \"{item.title}\" [{item.kind}, {item.category}] by "
            f"{item.author.name} ({followers}, {plays}, {age_years}y old)"
        )

    def _render(self, feed: Feed) -> str:
        focus = feed.focus_category or "something new"
        exploit = [
            recommendation
            for recommendation in feed.recommendations
            if not recommendation.mechanism.startswith("wildcard:")
        ]
        wildcards = [
            recommendation
            for recommendation in feed.recommendations
            if recommendation.mechanism.startswith("wildcard:")
        ]
        lines: list[str] = []
        if exploit:
            lines.append(
                f"Not just more {focus} -- from its neighbourhood (graph hop):"
            )
            for recommendation in exploit:
                lines.append(self._describe_item(recommendation))
                lines.append(f"    why: {recommendation.reason}")
        if wildcards:
            arm = feed.wildcard_arm
            lines.append(f"Wildcard drop [{arm}] -- no relation to what you know:")
            for recommendation in wildcards:
                lines.append(self._describe_item(recommendation))
                lines.append(f"    why: {recommendation.reason}")
        if not feed.recommendations:
            lines.append(
                "Nothing left to show that you have not seen -- the catalog "
                "is exhausted for this session."
            )
        lines.append(
            "Feedback? Say 'liked <n>' / 'ignored <n>' / 'disliked <n>' "
            "(1-based, top to bottom)."
        )
        return "\n".join(lines)

    # ── introspection ───────────────────────────────────────────────────
    def stats(self) -> dict:
        return self.mixer.stats()

    def engine_report(self) -> dict:
        report = engine_report(self.config)
        report["embedder"] = {
            "engine": "tfidf (scikit-learn)",
            "live": True,
            "dim": getattr(self.embedder, "dim", 0),
        }
        return dict(report)
