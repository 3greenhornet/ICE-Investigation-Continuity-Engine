"""serendipity/diversity.py — Mechanism C: stop the echo.

Three layers, matching the research text:

  1. category decay      score *= base ** k   (base 0.5; k = how many of
                        the last `recent_window` turns were the same
                        category) — Instagram's cluster downranking.
  2. hard re-roll rule   if the candidate's category equals one of the
                        last 2 turns' categories, multiply by 0.3 and let
                        the caller draw a replacement from the same slot.
  3. MMR                 when assembling a multi-item list, each new pick
                        maximizes λ·relevance − (1−λ)·max-similarity-to-
                        already-picked. λ=0.6 keeps it relevant while
                        actively repelling near-duplicates.

Similarity falls back gracefully: embedding cosine when both sides have
vectors, else tag-Jaccard, else 0 (no data -> no penalty, never a crash).
"""
import math
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


def tag_jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def default_similarity(x_tags, x_emb, y_tags, y_emb) -> float:
    if x_emb is not None and y_emb is not None:
        return max(0.0, cosine(x_emb, y_emb))
    return tag_jaccard(x_tags, y_tags)


def decay_multiplier(category: str, recent_categories: Sequence[str],
                     base: float = 0.5) -> float:
    """0.5 ** k over the recent window; 1.0 when the category is fresh."""
    if not category:
        return 1.0
    k = sum(1 for c in recent_categories if c == category)
    return base ** k if k else 1.0


def reroll_needed(category: str, last_two: Sequence[str]) -> bool:
    return bool(category) and category in list(last_two)


def mmr_select(
    candidates: List[str],
    relevance: Dict[str, float],
    similarity: Callable[[str, str], float],
    k: int,
    lambda_mult: float = 0.6,
) -> List[str]:
    """Classic MMR greedy selection. Deterministic; O(k·n)."""
    remaining = [c for c in candidates if c in relevance]
    if k <= 0 or not remaining:
        return []
    selected: List[str] = []
    # first pick: pure relevance
    first = max(remaining, key=lambda c: (relevance[c], c))
    selected.append(first)
    remaining.remove(first)
    while remaining and len(selected) < k:
        best, best_val = None, -math.inf
        for cand in remaining:
            max_sim = max((similarity(cand, s) for s in selected), default=0.0)
            val = lambda_mult * relevance[cand] - (1.0 - lambda_mult) * max_sim
            if val > best_val or (val == best_val and cand < (best or "")):
                best, best_val = cand, val
        selected.append(best)
        remaining.remove(best)
    return selected


def intralist_diversity(tags_of: Callable[[str], tuple],
                         items: List[str]) -> Optional[float]:
    """1 − mean pairwise tag-Jaccard. None for < 2 items."""
    if len(items) < 2:
        return None
    total, pairs = 0.0, 0
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            total += tag_jaccard(tags_of(a), tags_of(b))
            pairs += 1
    return 1.0 - (total / pairs if pairs else 0.0)
