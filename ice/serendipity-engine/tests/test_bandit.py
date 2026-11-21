import math
import random
import unittest

from serendipity.bandit import (
    posterior_mean,
    score_all,
    thompson_draw,
    ucb_score,
)


class TestBandit(unittest.TestCase):
    def test_ucb_cold_item_outranks_everything(self):
        # n_i = 0 -> +inf regardless of mean
        self.assertEqual(ucb_score(0.99, 0, 100), math.inf)
        # warm item: finite
        warm = ucb_score(0.9, 50, 1000, c=1.0)
        self.assertGreater(warm, 0.9)
        self.assertLess(warm, 1.6)

    def test_ucb_bonus_shrinks_with_exposure(self):
        n1 = ucb_score(0.5, 1, 10000)
        n9 = ucb_score(0.5, 9, 10000)
        self.assertGreater(n1, n9)  # less-seen gets the bigger spike

    def test_thompson_flat_beta_sometimes_wins(self):
        """The research claim: a flat Beta(1,1) curve occasionally beats
        an established favorite (0.95-ish). Over 4000 draws the cold item
        must win a meaningful share of rounds."""
        rng = random.Random(11)
        cold_wins = 0
        rounds = 4000
        for _ in range(rounds):
            favorite = thompson_draw(rng, 190, 10)   # ~0.95 tight curve
            newcomer = thompson_draw(rng, 0, 0)      # Beta(1,1) flat
            if newcomer > favorite:
                cold_wins += 1
        # expectation ~3-8%; assert a sane band, not a coincidence
        self.assertGreater(cold_wins, rounds * 0.01,
                            f"cold item won only {cold_wins}/{rounds}")
        self.assertLess(cold_wins, rounds * 0.15,
                        f"cold item won too often: {cold_wins}/{rounds}")

    def test_posterior_mean_stranger_is_neutral(self):
        self.assertAlmostEqual(posterior_mean(0, 0), 0.5, places=6)
        self.assertGreater(posterior_mean(20, 2), 0.85)
        self.assertLess(posterior_mean(2, 20), 0.15)

    def test_score_all_ucb_single_cold_winner_then_ranked(self):
        rng = random.Random(7)
        items = ["a", "b", "c"]
        glob = {"a": (0, 0), "b": (10, 5), "c": (30, 3)}
        s = score_all(items, lambda i: glob[i], "ucb", 1.0, rng, 1.0, 1.0)
        self.assertEqual(max(s, key=s.get), "a")
        self.assertGreater(s["b"], s["c"])  # 50% rate at n=10 beats 10% at n=30

    def test_score_all_thompson_prefers_good_rates(self):
        rng = random.Random(3)
        items = ["good", "bad"]
        glob = {"good": (50, 45), "bad": (50, 5)}
        wins = 0
        for _ in range(300):
            s = score_all(items, lambda i: glob[i], "thompson", 1.0, rng, 1.0, 1.0)
            if s["good"] > s["bad"]:
                wins += 1
        self.assertGreater(wins, 230, f"thompson favored good only {wins}/300")


if __name__ == "__main__":
    unittest.main()
