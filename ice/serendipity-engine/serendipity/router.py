"""serendipity/router.py — the 4-slot machine, plus the part the
research text got wrong-by-omission: the mixture should not stay fixed.

Static mode: 60/20/15/5 as specced.
Adaptive mode (default on): each slot's share is multiplied by its
observed engagement rate (Laplace-smoothed), then re-normalized under
hard floors — exploit never below 35% (relevance keeps a majority lane),
entropy never above 15% (wildcards never flood), every slot >= 5%.
Adapting only starts after adaptive_min_shown suggestions per slot,
so a lucky/unlucky first draw can't hijack the machine.
"""
import random
from typing import Dict, List, Tuple

SLOTS = ("exploit", "adjacent", "uncertainty", "entropy")


def effective_weights(
    base: Dict[str, float],
    slot_stats: Dict[str, Tuple[int, int]],   # slot -> (shown, engaged)
    adaptive: bool,
    min_shown: int,
    floor: float,
    exploit_floor: float,
    entropy_ceiling: float,
) -> Dict[str, float]:
    if not adaptive:
        w = normalize(base)
    else:
        raw: Dict[str, float] = {}
        for slot in SLOTS:
            shown, engaged = slot_stats.get(slot, (0, 0))
            if shown < min_shown:
                raw[slot] = base.get(slot, 0.0)
                continue
            rate = (engaged + 1) / (shown + 2)     # Laplace: 0..1, never 0
            raw[slot] = base.get(slot, 0.0) * (0.2 + 1.6 * rate)
        w = normalize(raw)
    # hard guardrails, kept after renormalization (bounded projection:
    # rescale, then clip to bounds, repeat until the simplex + bounds hold)
    bounds = {s: (floor, 1.0) for s in SLOTS}
    bounds["exploit"] = (max(floor, exploit_floor), 1.0)
    bounds["entropy"] = (floor, entropy_ceiling)
    for _ in range(6):
        total = sum(w.values())
        if total <= 0:
            w = {s: 0.25 for s in SLOTS}
            break
        scale = 1.0 / total
        w = {s: w[s] * scale for s in SLOTS}
        changed = False
        for s in SLOTS:
            lo, hi = bounds[s]
            if w[s] < lo - 1e-12:
                w[s] = lo
                changed = True
            elif w[s] > hi + 1e-12:
                w[s] = hi
                changed = True
        if not changed and abs(sum(w.values()) - 1.0) < 1e-9:
            break
    return w


def normalize(w: Dict[str, float]) -> Dict[str, float]:
    total = sum(v for v in w.values() if v > 0)
    if total <= 0:
        return {s: 0.25 for s in SLOTS}
    out = {s: (w.get(s, 0.0) / total if w.get(s, 0.0) > 0 else 0.0) for s in SLOTS}
    # fix rounding drift on the largest slot
    drift = 1.0 - sum(out.values())
    if abs(drift) > 1e-9:
        top = max(out, key=lambda s: out[s])
        out[top] += drift
    return out


def draw_slots(n: int, weights: Dict[str, float], rng: random.Random) -> List[str]:
    """Draw n slot labels. Names are shuffled so `rng.choices` ordering
    ties break fairly."""
    names = list(SLOTS)
    weights_list = [max(0.0, weights.get(s, 0.0)) for s in names]
    if sum(weights_list) <= 0:
        weights_list = [0.25] * 4
    return rng.choices(names, weights=weights_list, k=n)
