"""serendipity/models.py — plain data carriers, no behavior to break."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Item:
    """One suggestable thing (a curiosity node, a memory, a topic).

    Attributes:
        id: stable unique key (for ICE: the curiosity node id).
        title: human-readable text the chatbot can say aloud.
        tags: free-form label set; drives similarity, adjacency and the
            user interest profile when no embeddings exist.
        category: optional coarse bucket (taxonomy parent). Enables the
            Instagram-style sibling/cousin stepping and category decay.
        quality: 0..1 trustworthiness bar (junk never gets surfaced).
        embedding: optional vector; used for cosine similarity when present.
        n_shown_global: prior global exposure count (0 for a cold item,
            large for an "already famous" item). Seeds the global bandit.
    """
    id: str
    title: str
    tags: tuple = ()
    category: str = ""
    quality: float = 0.5
    embedding: Optional[List[float]] = None
    n_shown_global: int = 0


class FeedbackSignal:
    ENGAGED = "engaged"      # replied, clicked, stayed on topic  -> alpha += 1
    IGNORED = "ignored"      # subject change, no reaction         -> beta  += 1
    NEGATIVE = "negative"    # explicit dislike                    -> beta  += 2


@dataclass
class Suggestion:
    """What the engine hands back: the item, why it appeared, and the
    token that attributes the user's reaction to this exact decision."""
    token: str
    item: Item
    slot: str                    # exploit | adjacent | uncertainty | entropy
    reason: str                  # human-readable, chatbot can show it
    scores: Dict[str, float] = field(default_factory=dict)
    turn: int = 0


@dataclass
class DecisionRecord:
    """Audit entry: full provenance of one suggest() call."""
    turn: int
    user: str
    slots_drawn: List[str]
    candidates_considered: Dict[str, int]
    suppressed: List[str]        # item ids dropped by decay/reroll/dedupe/safety
    final: List[str]            # item ids in output order
    reasons: Dict[str, str] = field(default_factory=dict)
