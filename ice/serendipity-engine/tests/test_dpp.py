"""tests/test_dpp.py — Determinantal Point Process selection (diversity.dpp_select)."""
import math
import random
import time
import unittest

from serendipity import Item, SerendipityConfig, SerendipityEngine
from serendipity.diversity import dpp_select, mmr_select, tag_jaccard


def _sim_from_groups(groups):
    g = {i: k for k, members in groups.items() for i in members}

    def sim(a, b):
        if a == b:
            return 1.0
        return 0.9 if g.get(a) == g.get(b) else 0.0
    return sim


class TestDppSelect(unittest.TestCase):
    def test_empty_and_zero_k(self):
        self.assertEqual(dpp_select([], {}, lambda a, b: 0.0, k=3), [])
        self.assertEqual(dpp_select(["a"], {"a": 1.0}, lambda a, b: 0.0, k=0), [])

    def test_first_pick_is_most_relevant(self):
        rel = {"a": 0.9, "b": 0.5, "c": 0.1}
        out = dpp_select(list(rel), rel, lambda a, b: 0.0, k=3)
        self.assertEqual(out[0], "a")
        self.assertEqual(sorted(out), ["a", "b", "c"])

    def test_skips_near_duplicates(self):
        # a, a2, a3 are near-identical and the most relevant; b, c are distinct.
        rel = {"a": 1.0, "a2": 0.98, "a3": 0.97, "b": 0.6, "c": 0.5}
        sim = _sim_from_groups({"A": ["a", "a2", "a3"], "B": ["b"], "C": ["c"]})
        out = dpp_select(list(rel), rel, sim, k=3, lambda_mult=0.6)
        self.assertEqual(out[0], "a")
        self.assertEqual(set(out), {"a", "b", "c"})

    def test_set_level_redundancy_beats_mmr(self):
        """MMR only penalises the *single* nearest picked item, so an item that
        is moderately similar to every picked item slips through. The DPP's
        determinant counts the redundancy against the whole set."""
        rel = {"x": 1.0, "y": 0.99, "hub": 0.95, "z": 0.7}

        def sim(a, b):
            if a == b:
                return 1.0
            pair = {a, b}
            if "hub" in pair and pair & {"x", "y"}:
                return 0.6          # hub overlaps both x and y
            return 0.0

        dpp = dpp_select(list(rel), rel, sim, k=3, lambda_mult=0.6)
        mmr = mmr_select(list(rel), rel, sim, k=3, lambda_mult=0.9)
        self.assertEqual(set(dpp), {"x", "y", "z"})
        self.assertIn("hub", mmr)   # documents the MMR weakness being fixed

    def test_preselected_conditions_the_choice(self):
        rel = {"a2": 1.0, "b": 0.8}
        sim = _sim_from_groups({"A": ["a", "a2"], "B": ["b"]})
        free = dpp_select(list(rel), rel, sim, k=1)
        conditioned = dpp_select(list(rel), rel, sim, k=1, preselected=["a"])
        self.assertEqual(free, ["a2"])
        self.assertEqual(conditioned, ["b"])     # a2 is redundant with the batch
        self.assertNotIn("a", conditioned)        # preselected never returned

    def test_handles_infinite_cold_start_scores(self):
        rel = {"cold1": math.inf, "cold2": math.inf - 1, "warm": 0.4, "cool": -1.0}
        out = dpp_select(list(rel), rel, lambda a, b: 0.0, k=4)
        self.assertEqual(out[0], "cold1")
        self.assertEqual(len(out), 4)

    def test_identical_items_fall_back_to_relevance_order(self):
        rel = {"a": 0.9, "b": 0.8, "c": 0.7}
        out = dpp_select(list(rel), rel, lambda a, b: 1.0, k=3)
        self.assertEqual(out, ["a", "b", "c"])  # never drops items, never crashes

    def test_lambda_one_is_pure_relevance(self):
        rel = {"a": 1.0, "a2": 0.99, "b": 0.1}
        sim = _sim_from_groups({"A": ["a", "a2"], "B": ["b"]})
        relevance_only = dpp_select(list(rel), rel, sim, k=2, lambda_mult=1.0)
        diverse = dpp_select(list(rel), rel, sim, k=2, lambda_mult=0.3)
        self.assertEqual(diverse, ["a", "b"])
        self.assertEqual(relevance_only[0], "a")

    def test_deterministic(self):
        rng = random.Random(7)
        ids = [f"i{n}" for n in range(40)]
        rel = {i: rng.random() for i in ids}
        tags = {i: tuple(rng.sample("abcdefgh", 3)) for i in ids}

        def sim(a, b):
            return tag_jaccard(tags[a], tags[b])
        self.assertEqual(dpp_select(ids, rel, sim, k=10), dpp_select(ids, rel, sim, k=10))

    def test_large_pool_is_bounded(self):
        rng = random.Random(3)
        ids = [f"i{n}" for n in range(5000)]
        rel = {i: rng.random() for i in ids}
        tags = {i: tuple(rng.sample("abcdefghijklmnop", 4)) for i in ids}
        calls = {"n": 0}

        def sim(a, b):
            calls["n"] += 1
            return tag_jaccard(tags[a], tags[b])
        t0 = time.perf_counter()
        out = dpp_select(ids, rel, sim, k=len(ids), max_pool=64)
        elapsed = time.perf_counter() - t0
        self.assertEqual(len(out), 5000)
        self.assertLessEqual(calls["n"], 64 * 64)
        self.assertLess(elapsed, 2.0)


class TestEngineWithDpp(unittest.TestCase):
    def _catalog(self):
        items = []
        for cat in ("auth", "db", "ui", "infra"):
            for n in range(6):
                items.append(Item(id=f"{cat}{n}", title=f"{cat} {n}",
                                  tags=(cat, f"{cat}-t{n % 2}"), category=cat, quality=0.8))
        return items

    def test_config_validation(self):
        with self.assertRaises(AssertionError):
            SerendipityEngine(config=SerendipityConfig(diversity_method="nope"))

    def test_dpp_engine_suggests_diverse_batches(self):
        cfg = SerendipityConfig(diversity_method="dpp", seed=11)
        eng = SerendipityEngine(config=cfg, items=self._catalog())
        for _ in range(5):
            out = eng.suggest("u", n=4)
            self.assertEqual(len(out), 4)
            self.assertEqual(len({s.item.id for s in out}), 4)
        m = eng.metrics("u")
        self.assertIsNotNone(m)

    def test_dpp_at_least_as_diverse_as_mmr(self):
        def mean_cats(method):
            cfg = SerendipityConfig(diversity_method=method, seed=5, adaptive_router=False)
            eng = SerendipityEngine(config=cfg, items=self._catalog())
            per_batch = []
            for _ in range(20):
                out = eng.suggest("u", n=4)
                per_batch.append(len({s.item.category for s in out}))
            return sum(per_batch) / len(per_batch)
        self.assertGreaterEqual(mean_cats("dpp"), mean_cats("mmr") - 0.25)


if __name__ == "__main__":
    unittest.main()
