"""Configuration for the diversity-first chatbot recommender.

Every knob that governs the three mechanisms lives here so the behaviour
contract is inspectable in one place:

1. similar-but-different  -- retrieval restricted to *neighbour* categories
   (graph hop), reranked by MMR so the set is internally diverse.
2. wildcards              -- occasional slots drawn from long-tail / unknown
   author / orthogonal-topic / pure-random pools; which one (and whether a
   wildcard fires at all) is decided by a contextual multi-armed bandit.
3. anti-repetition        -- hard seen-set plus a semantic refractory port
   of the Hari engine's ``semantic_refractory`` idea (cosine suppression
   with decay over a window).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RecommenderConfig:
    # ── engine choices (swap without touching mechanism code) ────────────
    vector_engine: str = "qdrant"      # "qdrant" | "numpy"
    bandit_engine: str = "mab2rec"     # "mab2rec" | "vowpal_wabbit" | "epsilon"
    graph_engine: str = "networkx"     # "networkx" | "neo4j" (env-gated)
    # Gorse (gorse.io) and Neo4j GDS adapters exist but need live servers;
    # they are selected by environment variables, never silently:
    #   GORSE_ENDPOINT / NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD

    # ── mechanism 1: similar-but-different ───────────────────────────────
    mmr_lambda: float = 0.70           # 1.0 = pure relevance, 0.0 = pure diversity
    max_graph_hops: int = 2            # hop 1 first, hop 2 only to fill
    allow_same_category_fallback: bool = True  # last resort, flagged in the explanation
    neighbor_search_pool: int = 60     # candidates pulled from the vector store

    # ── mechanism 2: wildcards ───────────────────────────────────────────
    wildcard_arms: tuple = (
        "none",             # no wildcard this turn (bandit may learn to prefer it)
        "long_tail_old",    # the "old video with 100 plays"
        "unknown_author",   # the "no-name new author"
        "orthogonal_topic", # the "unrelated topic"
        "pure_random",      # completely random item, uniform over the catalog
    )
    long_tail_max_plays: int = 800
    long_tail_min_age_days: int = 365
    unknown_author_max_followers: int = 150
    orthogonal_min_graph_distance: int = 3   # categories at/after this hop count

    # ── mechanism 3: anti-repetition ────────────────────────────────────
    refractory_turns: int = 4           # suppression window (Hari semantic refractory)
    refractory_similarity: float = 0.55  # cosine above which an item is "too similar"
    refractory_max_penalty: float = 0.65  # strongest suppression multiplier floor
    seen_memory: int = 10000             # hard seen-set capacity

    # ── mixer ───────────────────────────────────────────────────────────
    feed_size: int = 5
    wildcard_slots_max: int = 1         # at most one wildcard slot per turn

    # ── reproducibility ─────────────────────────────────────────────────
    seed: int = 7

    extra: dict = field(default_factory=dict)
