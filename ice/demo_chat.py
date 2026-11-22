#!/usr/bin/env python3
"""demo/demo_chat.py — end-to-end proof that the mechanism works.

Part 1 (always runs): the same simulated user talks to TWO engines:
    ECHO  — router locked to 100% exploit (the "revolves solely around
            the user" chatbot you have today)
    FULL  — the 4-slot serendipity engine
  Same persona, same reaction rules, same seed family. We print the
  per-turn log for FULL, then a comparison table (coverage / Gini /
  serendipity rate / slot stats) and check the headline behaviors.

Part 2 (--ice, needs the ice repo on PYTHONPATH): drives the REAL
engine/curiosity_graph.CuriosityGraph through the adapter, shows a
suggestion cycle, and prints node exploration_progress BEFORE/AFTER
feedback — proving the previously-dead dial now moves.
"""
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)

from serendipity import SerendipityConfig, SerendipityEngine, Item

TAXO = {
    "painting": "fine_arts", "sculpture": "fine_arts", "pottery": "fine_arts",
    "fine_arts": "culture", "history": "culture", "music": "culture",
    "opera": "music", "jazz": "music",
    "physics": "science", "science": "nature",
    "cooking": "craft", "woodworking": "craft",
    "culture": "", "nature": "", "craft": "",
}

CATALOG = [
    Item("n_paint1", "Oil painting techniques", ("painting", "art"), "painting", 0.9, n_shown_global=120),
    Item("n_paint2", "Color theory for painters", ("painting", "art"), "painting", 0.85, n_shown_global=90),
    Item("n_paint3", "Sketching fundamentals", ("painting", "art"), "painting", 0.8, n_shown_global=75),
    Item("n_sculpt", "Basics of stone sculpture", ("sculpture", "art"), "sculpture", 0.8, n_shown_global=40),
    Item("n_pottery", "Wheel-thrown pottery", ("pottery", "art"), "pottery", 0.8, n_shown_global=15),
    Item("n_hist", "Why empires fall", ("history",), "history", 0.7, n_shown_global=60),
    Item("n_music", "How scales work", ("music",), "music", 0.75, n_shown_global=55),
    Item("n_opera", "Opera structure for newcomers", ("opera", "music"), "opera", 0.7, n_shown_global=25),
    Item("n_phys", "Black holes explained", ("physics",), "physics", 0.9, n_shown_global=200),
    Item("n_cook", "Sourdough starter science", ("cooking", "fermentation"), "cooking", 0.85, n_shown_global=70),
    Item("n_wood", "Japanese joinery without nails", ("woodworking", "craft"), "woodworking", 0.75, n_shown_global=8),
    Item("n_bridge", "Raspberry-Pi sous-vide cooking", ("cooking", "tech"), "cooking", 0.8, n_shown_global=3),
    Item("n_cold1", "A 17-year-old timelapse of coral growth", ("nature",), "physics", 0.6, n_shown_global=2),
    Item("n_cold2", "Handmade tool video with 100 views", ("craft",), "woodworking", 0.6, n_shown_global=1),
    Item("n_junk", "The secret the elites don't want you to know", ("conspiracy",), "history", 0.1, n_shown_global=500),
]

ADJ = {
    "n_paint1": [("n_paint2", 0.9), ("n_paint3", 0.8), ("n_sculpt", 0.6), ("n_hist", 0.2)],
    "n_paint2": [("n_paint1", 0.9), ("n_sculpt", 0.5), ("n_pottery", 0.3)],
    "n_paint3": [("n_paint1", 0.8), ("n_paint2", 0.7)],
    "n_sculpt": [("n_paint1", 0.6), ("n_paint2", 0.5), ("n_pottery", 0.7), ("n_hist", 0.3)],
    "n_pottery": [("n_paint2", 0.3), ("n_sculpt", 0.7), ("n_wood", 0.4)],
    "n_hist": [("n_paint1", 0.2), ("n_sculpt", 0.3), ("n_music", 0.4), ("n_opera", 0.3)],
    "n_music": [("n_hist", 0.4), ("n_opera", 0.6)],
    "n_opera": [("n_music", 0.6), ("n_hist", 0.3)],
    "n_phys": [("n_cold1", 0.5)],
    "n_cook": [("n_bridge", 0.8), ("n_cold1", 0.2)],
    "n_bridge": [("n_cook", 0.8)],
    "n_wood": [("n_pottery", 0.4)],
    "n_cold1": [("n_phys", 0.5), ("n_cook", 0.2)],
    "n_cold2": [], "n_junk": [],
}

# Persona: Sam. Loves painting+art history. Ignores sculpture the FIRST
# time, likes it from the second exposure (the learning arc the bandit
# must ride). Ignores cooking/physics/sports-ish stuff. Junk must never
# appear (safety). Cold wildcards: ignored, then never re-spammed.
LIKES = {"painting", "history", "music", "opera"}
LIKES_AFTER_2 = {"sculpture", "pottery", "woodworking"}
MEH = {"cooking", "physics"}


class Persona:
    def __init__(self):
        self.exposures = {}

    def react(self, item):
        if item.id == "n_junk":
            return "negative"
        cat = item.category
        if cat in LIKES:
            return "engaged"
        if cat in LIKES_AFTER_2:
            n = self.exposures.get(item.id, 0) + 1
            self.exposures[item.id] = n
            return "engaged" if n >= 2 else "ignored"
        if cat in MEH:
            return "ignored"
        return "ignored"


def build(seed, echo=False):
    cfg = SerendipityConfig(seed=seed)
    if echo:
        cfg.weights = {"exploit": 1.0, "adjacent": 0.0, "uncertainty": 0.0, "entropy": 0.0}
        cfg.adaptive_router = False
    cfg.min_quality = 0.3
    cfg.blocked_tags = ("conspiracy",)
    return SerendipityEngine(config=cfg, items=CATALOG, adjacency=ADJ, taxonomy=TAXO)


def run_engine(name, engine, turns=40, verbose=False):
    sam = Persona()
    pivots, cold_hits, junk_seen = [], [], 0
    sculpture_ignored_once = False
    for turn in range(1, turns + 1):
        picks = engine.suggest("sam", n=2, context_tags=("painting",) if turn % 3 else ())
        for p in picks:
            if p.item.id == "n_junk":
                junk_seen += 1
            sig = sam.react(p.item)
            engine.record(p.token, sig)
            if p.item.category in ("sculpture", "pottery") and p.slot == "adjacent":
                pivots.append((turn, p.item.title, sig))
            if p.item.id in ("n_cold1", "n_cold2", "n_bridge"):
                cold_hits.append((turn, p.slot, p.item.title))
            if p.item.category == "sculpture" and sig == "ignored":
                sculpture_ignored_once = True
            if verbose:
                mark = {"engaged": "[+]", "ignored": "[ ]", "negative": "[x]"}[sig]
                print(f"  t{turn:02d} {p.slot:11s} {mark} {p.item.title[:46]:46s} | {p.reason[:60]}")
    m = engine.metrics("sam")
    return {
        "name": name, "metrics": m, "pivots": pivots, "cold_hits": cold_hits,
        "junk_seen": junk_seen, "sculpture_ignored_once": sculpture_ignored_once,
    }


def main():
    print("=" * 78)
    print("PART 1 — echo chamber vs serendipity (same user, same reactions)")
    print("=" * 78)
    echo = run_engine("ECHO (100% exploit)", build(7, echo=True))
    full = run_engine("FULL (60/20/15/5)", build(7), verbose=True)

    rows = [
        ("coverage", lambda m: m["coverage"]),
        ("gini_exposure (lower=better)", lambda m: m["gini_exposure"]),
        ("entropy_exposure", lambda m: m["entropy_exposure"]),
        ("serendipity_rate", lambda m: m["serendipity_rate"]),
        ("intralist_diversity", lambda m: m["intralist_diversity"]),
    ]
    print(f"\n{'metric':32s} {'ECHO':>12s} {'FULL':>12s}")
    for label, fn in rows:
        e, f = fn(echo["metrics"]), fn(full["metrics"])
        print(f"{label:32s} {str(e):>12s} {str(f):>12s}")

    print("\nslot stats (FULL):")
    for slot, s in sorted(full["metrics"]["slots"].items()):
        print(f"  {slot:12s} shown={s['shown']:3d} engaged={s['engaged']:3d} "
              f"rate={s['engagement_rate']:.2f}")

    print(f"\nadjacent-lane pivots (painting -> sculpture/pottery): {len(full['pivots'])}")
    for t, title, sig in full["pivots"][:6]:
        print(f"  t{t:02d} {title} -> {sig}")
    print(f"\ncold-item surfacings (the 100-view engine): {len(full['cold_hits'])}")
    for t, slot, title in full['cold_hits'][:6]:
        print(f"  t{t:02d} via {slot:11s} {title}")
    print(f"\njunk ever shown: echo={echo['junk_seen']} full={full['junk_seen']} (safety filter)")
    print(f"sculpture needed a second exposure before landing: "
          f"{full['sculpture_ignored_once']} (the bandit riding the learning arc)")

    ok = (
        len(full["pivots"]) > 0
        and len(full["cold_hits"]) > 0
        and full["junk_seen"] == 0
        and echo["junk_seen"] == 0
        and (full["metrics"]["coverage"] or 0) > (echo["metrics"]["coverage"] or 0)
        and (full["metrics"]["gini_exposure"] or 1) < (echo["metrics"]["gini_exposure"] or 1)
    )
    print(f"\nDEMO VERDICT: {'PASS - the echo chamber broke' if ok else 'CHECK OUTPUT'}")

    if "--ice" in sys.argv:
        print("\n" + "=" * 78)
        print("PART 2 — live CuriosityGraph through the adapter (real ice/ code)")
        print("=" * 78)
        asyncio.run(ice_part())


async def ice_part():
    try:
        ice_root = os.environ.get("ICE_HOME") or os.path.dirname(os.path.abspath(__file__))
        if ice_root not in sys.path:
            sys.path.insert(0, ice_root)
        from engine.curiosity_graph import CuriosityGraph
    except Exception as exc:
        print(f"ice repo not importable ({exc}); skipping part 2")
        return

    g = CuriosityGraph()
    g._graph = __import__("networkx").Graph()
    for q in [
        "How do I improve my oil painting technique?",
        "Why did the Roman Republic fall?",
        "How does a sourdough starter actually work?",
        "What makes stone sculpture different from clay modeling?",
        "A 17-year-old timelapse of coral growth - what does it show?",
    ]:
        await g.add_node(q, importance=0.6)
    ids = list(g._graph.nodes)
    await g.update_edge(ids[0], ids[3], 0.5)   # painting <-> sculpture
    await g.update_edge(ids[1], ids[3], 0.4)   # history <-> sculpture
    await g.update_edge(ids[2], ids[4], 0.3)

    from ice_adapter.adapter import IceSerendipityAdapter
    adapter = IceSerendipityAdapter("demo_session", g)
    engine = adapter.build_engine(seed=21)

    before = {n: g._graph.nodes[n].get("exploration_progress", 0.0) for n in ids}
    picks = engine.suggest("demo_session", n=3)
    for p in picks:
        print(f"  suggest: {p.item.title[:60]} | slot={p.slot} | {p.reason[:58]}")
        engine.record(p.token, "engaged" if "painting" in p.item.tags or "history" in p.item.tags else "ignored")
    after = {n: g._graph.nodes[n].get("exploration_progress", 0.0) for n in ids}
    print("\n  exploration_progress on the REAL CuriosityGraph nodes:")
    for n in ids:
        delta = after[n] - before[n]
        if abs(delta) > 1e-9:
            print(f"    {g._graph.nodes[n]['core_question'][:48]:48s} {before[n]:.2f} -> {after[n]:.2f}")
    moved = any(abs(after[n] - before[n]) > 1e-9 for n in ids)
    print(f"\n  DEAD DIAL NOW MOVES: {'YES' if moved else 'NO'} "
          "(previously nothing in the whole repo ever updated this field)")


if __name__ == "__main__":
    main()
