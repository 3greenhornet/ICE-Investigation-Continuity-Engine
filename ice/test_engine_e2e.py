import os
import random
import tempfile
import unittest

from serendipity import SerendipityConfig, SerendipityEngine, Item, FeedbackSignal

TAXO = {
    "painting": "fine_arts", "sculpture": "fine_arts", "pottery": "fine_arts",
    "fine_arts": "culture", "history": "culture", "music": "culture",
    "physics": "science", "science": "nature", "cooking": "craft",
    "woodworking": "craft", "culture": "", "nature": "", "craft": "",
}

CATALOG = [
    Item("n_paint1", "Oil painting techniques", ("painting", "art"), "painting", 0.9, n_shown_global=120),
    Item("n_paint2", "Color theory for painters", ("painting", "art"), "painting", 0.85, n_shown_global=90),
    Item("n_sculpt", "Basics of stone sculpture", ("sculpture", "art"), "sculpture", 0.8, n_shown_global=40),
    Item("n_pottery", "Wheel-thrown pottery", ("pottery", "art"), "pottery", 0.8, n_shown_global=15),
    Item("n_hist", "Why empires fall", ("history",), "history", 0.7, n_shown_global=60),
    Item("n_music", "How scales work", ("music",), "music", 0.75, n_shown_global=55),
    Item("n_phys", "Black holes explained", ("physics",), "physics", 0.9, n_shown_global=200),
    Item("n_cook", "Sourdough starter science", ("cooking", "fermentation"), "cooking", 0.85, n_shown_global=70),
    Item("n_bridge", "Raspberry-Pi sous-vide cooking", ("cooking", "tech"), "cooking", 0.8, n_shown_global=3),
    Item("n_cold1", "A 17-year-old timelapse of coral growth", ("nature",), "science", 0.6, n_shown_global=2),
    Item("n_cold2", "Handmade tool video with 100 views", ("craft",), "woodworking", 0.6, n_shown_global=1),
    Item("n_junk", "Secret the elites don't want you to know", ("conspiracy",), "history", 0.1, n_shown_global=500),
]

ADJ = {
    "n_paint1": [("n_paint2", 0.9), ("n_sculpt", 0.6), ("n_hist", 0.2)],
    "n_paint2": [("n_paint1", 0.9), ("n_sculpt", 0.5), ("n_pottery", 0.3)],
    "n_sculpt": [("n_paint1", 0.6), ("n_paint2", 0.5), ("n_pottery", 0.7), ("n_hist", 0.3)],
    "n_pottery": [("n_paint2", 0.3), ("n_sculpt", 0.7)],
    "n_hist": [("n_paint1", 0.2), ("n_sculpt", 0.3), ("n_music", 0.4)],
    "n_music": [("n_hist", 0.4)],
    "n_phys": [("n_cold1", 0.5)],
    "n_cook": [("n_bridge", 0.8)],
    "n_bridge": [("n_cook", 0.8)],
    "n_cold1": [("n_phys", 0.5)],
    "n_cold2": [],
    "n_junk": [],
}


def make_engine(seed=99, **cfg_over):
    cfg = SerendipityConfig(seed=seed, **cfg_over)
    return SerendipityEngine(
        config=cfg, items=CATALOG, adjacency=ADJ, taxonomy=TAXO
    )


class TestEngineE2E(unittest.TestCase):
    def test_cold_user_gets_suggestions_never_empty(self):
        eng = make_engine()
        out = eng.suggest("sam", n=3)
        self.assertEqual(len(out), 3)
        for s in out:
            self.assertTrue(s.reason)
            self.assertIn(s.slot, ("exploit", "adjacent", "uncertainty", "entropy"))

    def test_single_item_catalog_still_returns(self):
        eng = SerendipityEngine(
            config=SerendipityConfig(seed=1),
            items=[Item("only", "The only topic", ("x",), "", 0.5)],
        )
        out = eng.suggest("u", n=2)
        self.assertTrue(out)                      # at least one; dedupe limits the second
        self.assertEqual(out[0].item.id, "only")

    def test_painting_to_sculpture_pivot_happens(self):
        """The headline behavior: a painting-loving user gets a sculpture
        suggestion through the adjacent lane within a session."""
        eng = make_engine()
        saw_sculpture_or_pottery = False
        # warm the taste: engage painting/history/music a few times
        for _round in range(24):
            picks = eng.suggest("sam", n=2, context_tags=("painting",))
            for p in picks:
                sig = "engaged" if p.item.category in ("painting", "history", "music") else "ignored"
                if p.item.category in ("sculpture", "pottery"):
                    saw_sculpture_or_pottery = True
                    sig = "engaged"   # sculpture lands on the 2nd exposure in the persona
                eng.record(p.token, sig)
        self.assertTrue(saw_sculpture_or_pottery,
                        "adjacent lane never stepped out of the painting cluster")

    def test_uncertainty_slot_surfaces_cold_items(self):
        """The 100-view engine: the uncertainty lane must spend its picks
        on the globally coldest items, never on the already-famous ones."""
        eng = make_engine(policy="ucb")
        first_uncertain = None
        surfaced = []
        for _ in range(40):
            picks = eng.suggest("sam", n=1)
            for p in picks:
                if p.slot == "uncertainty":
                    if first_uncertain is None:
                        first_uncertain = p.item.id
                    surfaced.append(p.item.id)
                eng.record(p.token, "ignored")
        cold = {"n_cold1", "n_cold2", "n_bridge"}
        famous = {"n_paint1", "n_paint2", "n_phys", "n_junk"}
        self.assertIn(first_uncertain, cold,
                      f"first uncertainty pick was {first_uncertain}, not a cold item")
        self.assertTrue(set(surfaced) & cold,
                        f"uncertainty never surfaced a cold item; got {surfaced}")
        self.assertFalse(set(surfaced) & famous,
                         f"popularity bias won the uncertainty lane: {surfaced}")

    def test_safety_never_surfaces_junk(self):
        eng = make_engine(min_quality=0.3, blocked_tags=("conspiracy",))
        seen_ids = set()
        for _ in range(60):
            for p in eng.suggest("sam", n=2):
                seen_ids.add(p.item.id)
                eng.record(p.token, "ignored")
        self.assertNotIn("n_junk", seen_ids)

    def test_entropy_respects_quality_bar(self):
        eng = make_engine(min_quality=0.0, entropy_min_quality=0.35)
        for _ in range(40):
            picks = eng.suggest("sam", n=1)
            for p in picks:
                if p.slot == "entropy":
                    self.assertGreaterEqual(p.item.quality, 0.35)
                eng.record(p.token, "ignored")

    def test_feedback_loop_updates_arms_and_slots(self):
        eng = make_engine()
        picks = eng.suggest("sam", n=4)
        engaged = [p for p in picks if p.item.category == "painting"] or picks[:1]
        ignored = [p for p in picks if p not in engaged][:1]
        for p in engaged:
            eng.record(p.token, "engaged")
        for p in ignored:
            eng.record(p.token, "ignored")
        a, b = eng._store.arm("sam", engaged[0].item.id)
        self.assertEqual(a, 1)
        a2, b2 = eng._store.arm("sam", ignored[0].item.id)
        self.assertEqual(b2, 1)
        # double-record rejected
        with self.assertRaises(ValueError):
            eng.record(engaged[0].token, "engaged")

    def test_negative_weighs_double(self):
        eng = make_engine(negative_beta_weight=2)
        p = eng.suggest("sam", n=1)[0]
        eng.record(p.token, "negative")
        _a, b = eng._store.arm("sam", p.item.id)
        self.assertEqual(b, 2)

    def test_same_category_not_suggested_back_to_back(self):
        eng = make_engine(weights={"exploit": 0.0, "adjacent": 0.0, "uncertainty": 0.0, "entropy": 1.0})
        last = None
        for _ in range(20):
            p = eng.suggest("sam", n=1)[0]
            if last is not None:
                self.assertFalse(
                    p.item.category and p.item.category == last,
                    "category repeated on consecutive turns",
                )
            last = p.item.category
            eng.record(p.token, "ignored")

    def test_determinism_same_seed_same_sequence(self):
        e1, e2 = make_engine(seed=1234), make_engine(seed=1234)
        s1 = e1.suggest("sam", n=3)
        s2 = e2.suggest("sam", n=3)
        self.assertEqual([x.item.id for x in s1], [x.item.id for x in s2])
        self.assertEqual([x.slot for x in s1], [x.slot for x in s2])

    def test_metrics_report_shape(self):
        eng = make_engine()
        for _ in range(10):
            for p in eng.suggest("sam", n=2):
                eng.record(p.token, "engaged" if p.item.quality > 0.7 else "ignored")
        m = eng.metrics("sam")
        for key in ("coverage", "gini_exposure", "entropy_exposure",
                    "slots", "serendipity_rate"):
            self.assertIn(key, m)
        self.assertGreater(m["coverage"], 0.0)

    def test_sqlite_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "s.sqlite")
            eng = SerendipityEngine(
                config=SerendipityConfig(seed=5, store_path=path),
                items=CATALOG, adjacency=ADJ, taxonomy=TAXO,
            )
            p = eng.suggest("sam", n=2)[0]
            eng.record(p.token, "engaged")
            blob = eng._store.export_json()
            # fresh instance on the same file sees the same counters
            eng2 = SerendipityEngine(
                config=SerendipityConfig(seed=5, store_path=path),
                items=CATALOG, adjacency=ADJ, taxonomy=TAXO,
            )
            a1, _ = eng._store.arm("sam", p.item.id)
            a2, _ = eng2._store.arm("sam", p.item.id)
            self.assertEqual(a1, a2)
            self.assertGreater(len(eng2._store.events("sam")), 0)
            # memory store import/export roundtrip
            eng3 = SerendipityEngine(
                config=SerendipityConfig(seed=5), items=CATALOG
            )
            eng3._store.import_json(blob)
            self.assertEqual(eng3._store.arm("sam", p.item.id)[0], a1)

    def test_audit_log_records_decisions(self):
        eng = make_engine()
        eng.suggest("sam", n=3)
        log = eng.decision_log()
        self.assertEqual(len(log), 1)
        self.assertEqual(len(log[0].final), 3)
        self.assertTrue(log[0].slots_drawn)


if __name__ == "__main__":
    unittest.main()
