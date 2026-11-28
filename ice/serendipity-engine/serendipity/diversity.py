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


def _quality_from_relevance(relevance: Dict[str, float], ids: List[str],
                            theta: float) -> Dict[str, float]:
    """Map arbitrary relevance scores (may contain +inf from cold-start UCB)
    to DPP quality terms q_i = exp(theta * r_i) with r_i min-max scaled to
    [0, 1]. Infinite scores rank above every finite one, in their order."""
    finite = [relevance[i] for i in ids if math.isfinite(relevance[i])]
    lo = min(finite) if finite else 0.0
    hi = max(finite) if finite else 1.0
    span = (hi - lo) or 1.0
    inf_ids = sorted((i for i in ids if not math.isfinite(relevance[i])),
                     key=lambda i: -relevance[i])
    out: Dict[str, float] = {}
    for i in ids:
        r = relevance[i]
        if math.isfinite(r):
            scaled = (r - lo) / span
        else:
            # +inf ranks first (1.0 + small bonus by position); -inf last.
            scaled = 1.0 + 0.05 * (len(inf_ids) - inf_ids.index(i)) if r > 0 else 0.0
        out[i] = math.exp(theta * scaled)
    return out


def dpp_select(
    candidates: List[str],
    relevance: Dict[str, float],
    similarity: Callable[[str, str], float],
    k: int,
    lambda_mult: float = 0.6,
    preselected: Sequence[str] = (),
    max_pool: int = 64,
    eps: float = 1e-10,
) -> List[str]:
    """Greedy MAP inference for a Determinantal Point Process.

    Kernel  L_ij = q_i · S_ij · q_j   (S_ii = 1, S = item similarity)
    Each step adds the item that most increases log det(L_Y) — i.e. the
    best combination of quality and *non-redundancy with the whole set*,
    not just with its single nearest neighbour (MMR's max-similarity).

    Uses the incremental Cholesky update of Chen, Zhang & Zhou,
    "Fast Greedy MAP Inference for DPP" (NeurIPS 2018): O(k·n) similarity
    calls and O(k²·n) arithmetic, pure Python (no NumPy needed).

    ``lambda_mult`` keeps MMR's meaning (1 = relevance only, 0 = diversity
    only) via the paper's trade-off theta = λ / (2(1-λ)).
    ``preselected`` items (e.g. already chosen for this batch) condition the
    process: candidates similar to them are discounted, but they are not
    returned. Candidates beyond ``max_pool`` (by relevance) are appended in
    relevance order, which bounds cost on large pools.
    Deterministic: ties break by id.
    """
    pool = [c for c in candidates if c in relevance]
    if k <= 0 or not pool:
        return []
    pool.sort(key=lambda c: (-relevance[c] if math.isfinite(relevance[c])
                             else (-math.inf if relevance[c] > 0 else math.inf), c))
    head, tail = pool[:max_pool], pool[max_pool:]

    lam = min(max(lambda_mult, 0.0), 0.999)
    theta = lam / (2.0 * (1.0 - lam)) if lam > 0 else 0.0
    q = _quality_from_relevance(relevance, head, theta)
    pre = [p for p in preselected if p not in q]

    def kern(a: str, b: str, qa: float, qb: float) -> float:
        s = 1.0 if a == b else max(0.0, min(1.0, similarity(a, b)))
        return qa * s * qb

    # Conditioning items get unit quality: they shape the geometry only.
    qual = dict(q)
    for p in pre:
        qual[p] = 1.0
    universe = pre + head
    cis: Dict[str, List[float]] = {i: [] for i in universe}
    d2: Dict[str, float] = {i: qual[i] * qual[i] for i in universe}
    active = list(universe)

    def take(j: str) -> None:
        """Add j to the selected set; update every other item's residual."""
        active.remove(j)
        sd = math.sqrt(d2[j])
        cj = cis[j]
        for i in active:
            e = (kern(j, i, qual[j], qual[i])
                 - sum(a * b for a, b in zip(cj, cis[i]))) / sd
            cis[i].append(e)
            d2[i] -= e * e

    for p in pre:  # forced first choices: the batch picked so far
        if d2[p] > eps:
            take(p)
        else:
            active.remove(p)

    order: List[str] = []
    while active and len(order) < k:
        j, best = None, -math.inf
        for i in active:              # active keeps relevance order -> stable ties
            if d2[i] > best + 1e-15:
                j, best = i, d2[i]
        if j is None or best <= eps:
            break  # the rest is (numerically) redundant with the chosen set
        order.append(j)
        take(j)

    # Redundant / overflow items keep relevance order behind the DPP picks.
    rest = [i for i in head if i not in order] + tail
    return (order + rest)[:k]


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
