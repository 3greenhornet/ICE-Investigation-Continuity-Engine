"""serendipity/bandit.py — the uncertainty math.

Two tables, deliberately kept apart (the research conflates them):

  per-user arms  — (alpha, beta) per (user, item). Learns THIS user's
                   taste. Feeds the exploit slot's ranking.
  global counters — (shown, engaged) per item ACROSS everyone. This is
                   what surfaces the "100-view" item: an item barely
                   anyone has seen gets an uncertainty spike regardless
                   of who is chatting.

The Beta sampler is Python's stdlib `random.betavariate` — a maintained,
battle-tested implementation. We do not hand-roll the distribution; we
orchestrate it. That is the honest version of "don't reinvent the wheel".
"""
import math
import random
from typing import Dict, List, Tuple


def thompson_draw(rng: random.Random, alpha: int, beta: int,
                  prior_a: float = 1.0, prior_b: float = 1.0) -> float:
    """One Beta(α + prior_a, β + prior_b) draw in [0, 1].

    A brand-new item (0,0) draws from Beta(1,1) — the flat, wide curve:
    usually mediocre, occasionally 0.95, which is exactly how an obscure
    topic beats the user's favorite for one turn."""
    a = max(1e-6, alpha + prior_a)
    b = max(1e-6, beta + prior_b)
    return rng.betavariate(a, b)


def ucb_score(mean: float, n: int, total: int, c: float = 1.0) -> float:
    """Score(i) = μ_i + c·sqrt(ln N / n_i), with the cold-start convention:

    n_i = 0  ->  +inf  (a never-shown item outranks everything once,
                        then decays as it gets exposure)
    N = 0    ->  treated as 1 (ln 1 = 0: no bonus when nothing has
                        happened yet, so the formula never explodes on
                        a virgin system)."""
    if n <= 0:
        return math.inf
    N = max(1, total)
    return mean + c * math.sqrt(math.log(N) / n)


def posterior_mean(alpha: int, beta: int,
                   prior_a: float = 1.0, prior_b: float = 1.0) -> float:
    """μ̂ = (α + prior_a) / (α + β + prior_a + prior_b). The exploit slot's
    relevance backbone; 0.5 for a complete stranger (no strong opinion)."""
    return (alpha + prior_a) / (alpha + beta + prior_a + prior_b + 1e-9)


def score_all(
    items: List[str],
    get_global,          # item -> (shown, engaged)
    policy: str,
    c: float,
    rng: random.Random,
    prior_a: float,
    prior_b: float,
) -> Dict[str, float]:
    """Score every item for the uncertainty slot using GLOBAL counters.

    ucb      — deterministic; ties at +inf (all-cold) are broken randomly.
    thompson — one Beta draw per item from global (engaged, shown-engaged)."""
    out: Dict[str, float] = {}
    cold_ties: List[str] = []
    total_shown = 0
    for it in items:
        shown, engaged = get_global(it)
        total_shown += shown

    for it in items:
        shown, engaged = get_global(it)
        if policy == "ucb":
            mean = engaged / shown if shown > 0 else 0.0
            s = ucb_score(mean, shown, total_shown, c)
            if math.isinf(s):
                cold_ties.append(it)
                out[it] = s
            else:
                out[it] = s
        else:  # thompson
            # global n_i maps to Beta(engaged + prior, ignored + prior)
            ignored = max(0, shown - engaged)
            out[it] = thompson_draw(rng, engaged, ignored, prior_a, prior_b)
    if policy == "ucb" and cold_ties:
        # Several all-cold items tie at +inf. Rank them randomly below
        # the single +inf winner (inf-1, inf-2, ...) instead of excluding
        # them: a multi-suggestion call still gets cold candidates.
        rng.shuffle(cold_ties)
        for rank, it in enumerate(cold_ties):
            out[it] = math.inf if rank == 0 else math.inf - 1 - rank
    return out
