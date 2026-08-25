"""P6: the promoted blend and the new-manager shrink decay with evidence."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "v2"))

import season_view as SV  # noqa: E402


MODEL = dict(atk={"ARS": 0.5, "MCI": 0.4, "COV": -0.2, "IPS": -0.3, "BOU": 0.0},
             dfn={"ARS": 0.3, "MCI": 0.2, "COV": -0.4, "IPS": -0.1, "BOU": 0.0},
             promoted_prior={"atk": -0.337, "dfn": -0.2}, home_adv=0.18, rho=-0.05)
SHORTS = ["ARS", "MCI", "COV", "IPS", "BOU", "HUL"]


class DecayArithmetic(unittest.TestCase):
    def test_promoted_prior_weight_starts_full_and_halves_at_k(self):
        self.assertEqual(SV.promoted_prior_weight(0), 1.0)
        self.assertAlmostEqual(SV.promoted_prior_weight(SV.PROMOTED_K), 0.5)
        self.assertAlmostEqual(SV.promoted_prior_weight(0, w0=0.6), 0.6)
        self.assertAlmostEqual(SV.promoted_prior_weight(SV.PROMOTED_K, w0=0.6), 0.3)
        w = [SV.promoted_prior_weight(n) for n in range(0, 39)]
        self.assertEqual(w, sorted(w, reverse=True))

    def test_manager_shrink_starts_at_base_and_relaxes(self):
        self.assertEqual(SV.manager_shrink(0), SV.MANAGER_SHRINK)
        self.assertAlmostEqual(SV.manager_shrink(SV.MANAGER_K),
                               SV.MANAGER_SHRINK + (1 - SV.MANAGER_SHRINK) * 0.5)
        self.assertLess(SV.manager_shrink(38), 1.0)
        self.assertGreater(SV.manager_shrink(38), 0.9)


class AdjustRatings(unittest.TestCase):
    def test_no_matches_reproduces_the_pre_season_adjustments(self):
        atk, dfn, notes = SV.adjust_ratings(MODEL, SHORTS, {}, new_manager={"MCI"},
                                            promoted={"COV", "IPS"},
                                            promoted_blend={"COV": 1.0, "IPS": 0.6})
        known = ["ARS", "MCI", "COV", "IPS", "BOU"]
        mean_a = sum(MODEL["atk"][t] for t in known) / 5
        mean_d = sum(MODEL["dfn"][t] for t in known) / 5
        pa, pdf = mean_a - 0.337, mean_d - 0.2
        self.assertAlmostEqual(atk["COV"], pa)                    # discarded entirely at n=0
        self.assertAlmostEqual(atk["HUL"], pa)                    # no record -> prior
        self.assertAlmostEqual(dfn["HUL"], pdf)
        self.assertAlmostEqual(atk["IPS"], 0.6 * pa + 0.4 * -0.3)
        self.assertAlmostEqual(atk["MCI"], mean_a + (0.4 - mean_a) * 0.8)
        self.assertAlmostEqual(atk["ARS"], 0.5)                   # untouched
        self.assertIn("after 0 matches", notes["COV"])

    def test_matches_move_promoted_and_new_manager_clubs_towards_their_fit(self):
        n = {"COV": 30, "MCI": 15, "IPS": 30}
        atk, dfn, _ = SV.adjust_ratings(MODEL, SHORTS, n, new_manager={"MCI"},
                                        promoted={"COV", "IPS"},
                                        promoted_blend={"COV": 1.0, "IPS": 0.6})
        known = ["ARS", "MCI", "COV", "IPS", "BOU"]
        mean_a = sum(MODEL["atk"][t] for t in known) / 5
        pa = mean_a - 0.337
        self.assertAlmostEqual(atk["COV"], 0.5 * pa + 0.5 * -0.2)
        self.assertAlmostEqual(atk["IPS"], 0.3 * pa + 0.7 * -0.3)
        self.assertAlmostEqual(atk["MCI"], mean_a + (0.4 - mean_a) * 0.9)
        # and the fixed variant ignores n entirely
        atk_fixed, _, _ = SV.adjust_ratings(MODEL, SHORTS, n, new_manager={"MCI"},
                                            promoted={"COV", "IPS"},
                                            promoted_blend={"COV": 1.0, "IPS": 0.6},
                                            decay=False)
        self.assertAlmostEqual(atk_fixed["COV"], pa)
        self.assertAlmostEqual(atk_fixed["MCI"], mean_a + (0.4 - mean_a) * 0.8)

    def test_defence_shrinks_towards_the_league_mean_not_zero(self):
        atk, dfn, _ = SV.adjust_ratings(MODEL, SHORTS, {}, new_manager={"MCI"},
                                        promoted=set(), promoted_blend={})
        known = ["ARS", "MCI", "COV", "IPS", "BOU"]
        mean_d = sum(MODEL["dfn"][t] for t in known) / 5
        self.assertAlmostEqual(dfn["MCI"], mean_d + (0.2 - mean_d) * 0.8)


if __name__ == "__main__":
    unittest.main()
