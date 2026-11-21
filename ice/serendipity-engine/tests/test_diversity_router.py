import random
import unittest
from collections import Counter

from serendipity.diversity import (
    decay_multiplier,
    intralist_diversity,
    mmr_select,
    reroll_needed,
    tag_jaccard,
)
from serendipity.router import SLOTS, draw_slots, effective_weights, normalize


class TestDiversity(unittest.TestCase):
    def test_mmr_favors_variety_over_greedy(self):
        rel = {"a": 0.9, "a2": 0.85, "b": 0.8, "c": 0.7}
        sim = lambda x, y: 0.9 if {x, y} == {"a", "a2"} else 0.1
        greedy = sorted(rel, key=lambda k: -rel[k])[:3]
        mmr = mmr_select(list(rel), rel, sim, k=3, lambda_mult=0.6)
        # greedy takes a+a2 (near-duplicates); MMR must reject a2
        self.assertEqual(greedy[:2], ["a", "a2"])
        self.assertIn("a", mmr)
        self.assertNotIn("a2", mmr)
        self.assertIn("b", mmr)

    def test_decay_multiplier(self):
        recent = ["art", "art", "science"]
        self.assertEqual(decay_multiplier("art", recent, base=0.5), 0.25)
        self.assertEqual(decay_multiplier("science", recent, base=0.5), 0.5)
        self.assertEqual(decay_multiplier("cooking", recent, base=0.5), 1.0)

    def test_reroll_rule(self):
        self.assertTrue(reroll_needed("art", ["art", "science"]))
        self.assertFalse(reroll_needed("art", ["science", "history"]))
        self.assertFalse(reroll_needed("", ["art", "art"]))

    def test_intralist(self):
        tags = {
            "x": ("art",), "y": ("art",), "z": ("cooking",),
            "w": ("physics",),
        }
        d1 = intralist_diversity(tags.get, ["x", "y"])       # twins -> low
        d2 = intralist_diversity(tags.get, ["x", "z", "w"])  # spread -> high
        self.assertLess(d1, 0.3)
        self.assertGreater(d2, 0.6)
        self.assertIsNone(intralist_diversity(tags.get, ["x"]))

    def test_jaccard(self):
        self.assertEqual(tag_jaccard(("a", "b"), ("a", "b")), 1.0)
        self.assertEqual(tag_jaccard(("a",), ("b",)), 0.0)
        self.assertAlmostEqual(tag_jaccard(("a", "b"), ("b", "c")), 1 / 3)


class TestRouter(unittest.TestCase):
    def test_draw_distribution_matches_weights(self):
        rng = random.Random(42)
        w = {"exploit": 0.60, "adjacent": 0.20, "uncertainty": 0.15, "entropy": 0.05}
        n = 20000
        draws = draw_slots(n, w, rng)
        c = Counter(draws)
        for slot, target in w.items():
            got = c.get(slot, 0) / n
            self.assertLess(abs(got - target), 0.02, f"{slot}: {got} vs {target}")

    def test_normalize_fixes_drift(self):
        w = normalize({"exploit": 0.6, "adjacent": 0.2, "uncertainty": 0.15, "entropy": 0.05})
        self.assertAlmostEqual(sum(w.values()), 1.0, places=9)

    def test_adaptive_shifts_toward_winning_slot(self):
        base = {"exploit": 0.60, "adjacent": 0.20, "uncertainty": 0.15, "entropy": 0.05}
        # adjacent slot: 30 shown, 28 engaged -> the machine leans into it
        stats = {s: (0, 0) for s in SLOTS}
        stats["adjacent"] = (30, 28)
        stats["exploit"] = (30, 3)
        w = effective_weights(base, stats, adaptive=True, min_shown=8,
                               floor=0.05, exploit_floor=0.35, entropy_ceiling=0.15)
        self.assertGreater(w["adjacent"], base["adjacent"])
        self.assertLess(w["exploit"], base["exploit"] * 1.01)
        # floors respected
        self.assertGreaterEqual(w["exploit"], 0.35)
        self.assertLessEqual(w["entropy"], 0.15 + 1e-9)
        for s in SLOTS:
            self.assertGreaterEqual(w[s], 0.05)

    def test_adaptive_ignores_thin_evidence(self):
        base = {"exploit": 0.60, "adjacent": 0.20, "uncertainty": 0.15, "entropy": 0.05}
        stats = {"adjacent": (2, 2)}   # 2 shows: not enough to adapt
        stats = {s: stats.get(s, (0, 0)) for s in SLOTS}
        w = effective_weights(base, stats, adaptive=True, min_shown=8,
                               floor=0.05, exploit_floor=0.35, entropy_ceiling=0.15)
        # proportions unchanged (all below min_shown)
        for s in SLOTS:
            self.assertAlmostEqual(w[s], base[s] / sum(base.values()), places=6)


if __name__ == "__main__":
    unittest.main()
