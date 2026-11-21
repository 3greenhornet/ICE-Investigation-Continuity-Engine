import random
import unittest
from collections import defaultdict

from serendipity.taxonomy import Taxonomy, multimodal_bridges
from serendipity.walk import biased_random_walk, hop_distance

TAXO = {
    "painting": "fine_arts", "sculpture": "fine_arts", "pottery": "fine_arts",
    "fine_arts": "culture",
    "history": "culture", "music": "culture",
    "opera": "music", "jazz": "music",
    "physics": "science", "biology": "science", "science": "nature",
    "cooking": "craft", "woodworking": "craft",
    "culture": "", "nature": "", "craft": "",
}


class TestTaxonomy(unittest.TestCase):
    def setUp(self):
        self.tax = Taxonomy(TAXO)

    def test_siblings_painting_vs_sculpture(self):
        sibs = self.tax.siblings("painting")
        self.assertIn("sculpture", sibs)
        self.assertIn("pottery", sibs)
        self.assertNotIn("painting", sibs)

    def test_cousins_cross_family(self):
        # painting -> cousins: children of fine_arts' siblings (music's
        # children opera/jazz), NOT painting's own siblings
        cous = self.tax.cousins("painting")
        self.assertIn("opera", cous)
        self.assertIn("jazz", cous)
        self.assertNotIn("sculpture", cous)  # sibling, not cousin
        self.assertNotIn("painting", cous)

    def test_adjacent_categories_dedup_order(self):
        adj = self.tax.adjacent_categories("painting")
        self.assertIn("sculpture", adj)   # sibling
        self.assertIn("opera", adj)       # cousin
        self.assertNotIn("history", adj)  # parent's sibling, one level beyond
        self.assertEqual(len(adj), len(set(adj)))

    def test_lca_depth(self):
        self.assertEqual(self.tax.lca_depth("painting", "sculpture"), 2)
        self.assertEqual(self.tax.lca_depth("painting", "opera"), 4)
        self.assertIsNone(self.tax.lca_depth("painting", "physics"))
        self.assertEqual(self.tax.lca_depth("painting", "painting"), 0)


class TestMultimodal(unittest.TestCase):
    def test_bridges_need_two_hits(self):
        items = [
            ("bridge", "Raspberry-Pi sous-vide cooking", ("cooking", "tech")),
            ("single", "sourdough basics", ("cooking",)),
            ("unrelated", "quantum tunneling", ("physics",)),
        ]
        out = multimodal_bridges(items, ["cooking", "tech"])
        self.assertEqual(out, ["bridge"])


class TestWalk(unittest.TestCase):
    def _chain(self, n):
        adj = {}
        names = [f"n{i}" for i in range(n)]
        for i in range(n - 1):
            adj.setdefault(names[i], []).append((names[i + 1], 1.0))
            adj.setdefault(names[i + 1], []).append((names[i], 1.0))
        return adj, names

    def test_walk_length_controls_reach(self):
        """The serendipity dial: short walks stay near the seed, longer
        walks reach farther nodes. Verified by graph distance of the
        top-visited node."""
        adj, names = self._chain(12)
        seed = names[0]
        for L, max_hop in ((2, 3), (5, 7)):
            rng = random.Random(5)
            results = {}
            for _trial in range(20):   # average over restart randomness
                res = biased_random_walk(
                    adj, [seed], L, rng, walks_per_seed=40, restart_prob=0.0
                )
                for k, v in res.items():
                    results[k] = results.get(k, 0.0) + v
            top = max(results, key=results.get)
            hop = hop_distance(adj, seed, top)
            self.assertIsNotNone(hop)
            self.assertLessEqual(hop, max_hop, f"L={L} drifted too far: {hop}")
        # and at L=2 the top-visited node is close (<= 3 hops)
        rng = random.Random(5)
        res2 = biased_random_walk(adj, [seed], 2, rng, walks_per_seed=60, restart_prob=0.0)
        top2 = max(res2, key=res2.get)
        self.assertLessEqual(hop_distance(adj, seed, top2), 3)

    def test_restart_keeps_walk_anchored(self):
        adj, names = self._chain(12)
        rng = random.Random(1)
        res = biased_random_walk(
            adj, [names[0]], 8, rng, walks_per_seed=50, restart_prob=0.6
        )
        self.assertTrue(res)  # never empty on a connected graph
        hops = [hop_distance(adj, names[0], k) for k in res]
        avg = sum(h for h in hops if h) / len([h for h in hops if h])
        self.assertLess(avg, 5.0)  # heavy restart keeps it near the seed

    def test_empty_inputs_never_crash(self):
        self.assertEqual(biased_random_walk({}, ["x"], 3, random.Random(0)), {})
        self.assertEqual(biased_random_walk({"a": [("b", 1.0)]}, ["zzz"], 3, random.Random(0)), {})


if __name__ == "__main__":
    unittest.main()
