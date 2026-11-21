"""serendipity/config.py — every dial of the engine, one place.

Each constant maps to a mechanism in the research the user supplied
(YouTube/Instagram/Pinterest exploration architectures). The mapping is
documented in docs/ALGORITHMS.md. Every dial has a safe default; hosts
only touch what they want to tune.
"""
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class SerendipityConfig:
    # --- Router (the 4-slot machine) --------------------------------
    # research blueprint: 60% exploitation / 20% sibling-adjacent /
    # 15% uncertainty (UCB|Thompson) / 5% pure entropy.
    weights: Dict[str, float] = field(
        default_factory=lambda: {
            "exploit": 0.60,
            "adjacent": 0.20,
            "uncertainty": 0.15,
            "entropy": 0.05,
        }
    )
    # Learn the mixture from engagement instead of keeping it fixed.
    # Floors keep the machine from collapsing into a single slot.
    adaptive_router: bool = True
    adaptive_min_shown: int = 8      # per slot, before adaptation kicks in
    adaptive_floor: float = 0.05     # no slot below this share
    exploit_floor: float = 0.35      # relevance always keeps a majority lane
    entropy_ceiling: float = 0.15    # wildcards never flood the feed

    # --- Uncertainty slot (the "100-view engine") --------------------
    # policy: "ucb" (deterministic spike, surfaces the coldest item) or
    # "thompson" (Beta draws, softer organic randomness).
    policy: str = "ucb"
    ucb_c: float = 1.0               # exploration constant c
    prior_alpha: float = 1.0         # Beta(1,1) = flat, wide curve
    prior_beta: float = 1.0          #   -> brand-new items occasionally win

    # --- Walk (Pixie-style, Mechanism A) ------------------------------
    walk_length: int = 3             # L: 2 = hyper-relevant ... 6 = far drift
    walk_restarts: float = 0.15      # chance to jump back to a seed per step
    walk_walks_per_seed: int = 24

    # --- Diversity (MMR + decay, Mechanism C) -------------------------
    mmr_lambda: float = 0.6          # relevance vs. anti-repetition slider
    decay_base: float = 0.5          # score *= base ** k (k = recent repeats)
    reroll_penalty: float = 0.3      # same category as last 2 turns
    recent_window: int = 2          # turns a category stays "hot"
    dedupe_window: int = 6          # don't re-suggest an item within N turns

    # --- Entropy slot (the "17-year-old video" wildcard) --------------
    entropy_pool_fraction: float = 0.10   # bottom 10% least-shown globally
    entropy_min_quality: float = 0.35     # wildcards must still clear a bar

    # --- Feedback (the α/β loop) --------------------------------------
    negative_beta_weight: float = 2.0     # explicit dislike hurts more

    # --- Safety --------------------------------------------------------
    blocked_tags: tuple = ()
    blocked_categories: tuple = ()
    min_quality: float = 0.0

    # --- Housekeeping ----------------------------------------------------
    seed: Optional[int] = None      # None = fresh randomness; int = replay
    audit: bool = True              # decision log with scores + reasons
    store_path: Optional[str] = None  # None = in-memory; path = SQLite file

    def validate(self) -> None:
        assert self.policy in ("ucb", "thompson"), "policy must be ucb|thompson"
        total = sum(self.weights.values())
        assert total > 0, "router weights must sum > 0"
        for name in ("exploit", "adjacent", "uncertainty", "entropy"):
            assert name in self.weights, f"missing slot: {name}"
        assert 0.0 <= self.mmr_lambda <= 1.0
        assert 0.0 < self.decay_base <= 1.0
        assert 0.0 <= self.entropy_pool_fraction <= 1.0
