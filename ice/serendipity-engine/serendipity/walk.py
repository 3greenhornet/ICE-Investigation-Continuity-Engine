"""serendipity/walk.py — Pixie-style biased random walks (Mechanism A).

Pinterest's Pixie finds "related yet unexpected" pins by taking short
BIASED RANDOM WALKS from the user's pins over the pin-board graph. The
walk length L is the serendipity dial: L=2 lands on near neighbors
(more of the same), L=5-6 drifts across the graph (painting -> sculpture
-> pottery -> architecture).

This engine walks whatever graph the host hands it. For the Hari/ICE
adapter that graph is the EXISTING CuriosityGraph (networkx, persisted
in Postgres, edges already learn via Hebbian co-activation) — we read
its adjacency and never write to it.
"""
import random
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Sequence, Tuple

Adjacency = Dict[str, List[Tuple[str, float]]]


def normalize_adjacency(raw: Adjacency) -> Adjacency:
    """Drop non-positive edges; keep structure cheap for the walker."""
    out: Adjacency = {}
    for node, edges in raw.items():
        kept = [(nbr, w) for nbr, w in edges if w > 0.0]
        if kept:
            out[node] = kept
    return out


def biased_random_walk(
    adjacency: Adjacency,
    seeds: Sequence[str],
    walk_length: int,
    rng: random.Random,
    walks_per_seed: int = 24,
    restart_prob: float = 0.15,
    weight_pow: float = 1.0,
) -> Dict[str, float]:
    """Run short biased walks; return visit scores {node: normalized count}.

    Mechanics per step:
      * with prob restart_prob jump back to a random seed (Pixie keeps its
        walks anchored to the query pins; this stops deep drift when L is
        misconfigured large);
      * otherwise move to a neighbor chosen with probability proportional
        to weight ** weight_pow (weight_pow > 1 sharpens toward strong
        edges; < 1 flattens toward uniform, i.e. more surprising hops).

    Seeds themselves get no visit credit. Returns {} on empty input.
    """
    adj = normalize_adjacency(adjacency)
    seeds = [s for s in seeds if s in adj]
    if not seeds or walk_length <= 0:
        return {}

    visits: Dict[str, int] = defaultdict(int)
    for seed in seeds:
        for _ in range(max(1, walks_per_seed)):
            current = seed
            for _step in range(walk_length):
                if rng.random() < restart_prob:
                    current = rng.choice(seeds)
                    continue
                edges = adj.get(current)
                if not edges:
                    current = rng.choice(seeds)   # dead end: re-anchor
                    continue
                if weight_pow == 1.0:
                    pick = rng.choices(
                        [n for n, _ in edges], weights=[w for _, w in edges]
                    )[0]
                else:
                    weights = [w ** weight_pow for _, w in edges]
                    pick = rng.choices(
                        [n for n, _ in edges], weights=weights
                    )[0]
                current = pick
                if current not in seeds:
                    visits[current] += 1

    if not visits:
        return {}
    peak = max(visits.values())
    return {node: count / peak for node, count in visits.items()}


def hop_distance(adjacency: Adjacency, start: str, target: str, cap: int = 6) -> Optional[int]:
    """BFS shortest path length, for tests and for walk-dial diagnostics."""
    adj = normalize_adjacency(adjacency)
    if start == target:
        return 0
    frontier = {start}
    seen = {start}
    for depth in range(1, cap + 1):
        nxt = set()
        for node in frontier:
            for nbr, _w in adj.get(node, []):
                if nbr not in seen:
                    seen.add(nbr)
                    nxt.add(nbr)
                    if nbr == target:
                        return depth
        if not nxt:
            return None
        frontier = nxt
    return None
