import unittest

import numpy as np

from v2 import threshold_sweep as sweep


class ThresholdSweepTests(unittest.TestCase):
    @staticmethod
    def _draw(seed=20260902, seasons=6000):
        rng = np.random.default_rng(seed)
        gains = sweep.draw_gains(rng, (seasons, 38))
        noise = rng.standard_normal((seasons, 38))
        return gains, noise

    def test_seeded_sweep_is_deterministic(self):
        gains_a, noise_a = self._draw(seed=37, seasons=32)
        gains_b, noise_b = self._draw(seed=37, seasons=32)

        totals_a = sweep.simulate_totals(gains_a, noise_a, 2.3)
        totals_b = sweep.simulate_totals(gains_b, noise_b, 2.3)

        np.testing.assert_array_equal(gains_a, gains_b)
        np.testing.assert_array_equal(noise_a, noise_b)
        np.testing.assert_array_equal(totals_a, totals_b)

    def test_three_week_bank_and_hit_arithmetic(self):
        total, acts, boundaries = sweep.play_season(
            np.array([2.0, 5.0, 8.0]),
            np.array([0.0, 0.0, 8.0]),
            threshold=2.0,
        )
        self.assertEqual(acts, [2])
        self.assertEqual(boundaries, [1.0, 2.0, 3.0, 3.0])
        self.assertEqual(total, 8.0)

        total, acts, boundaries = sweep.play_season(
            np.array([5.0, 5.0, 9.0]),
            np.array([5.0, 5.0, 9.0]),
            threshold=1.0,
        )
        self.assertEqual(acts, [0, 1, 2])
        self.assertEqual(boundaries, [1.0, 1.0, 1.0, 1.0])
        self.assertEqual(total, 19.0)

    def test_empty_bank_hit_is_followed_by_weekly_allowance(self):
        total, acts, boundaries = sweep.play_season(
            np.array([9.0, 5.0]), np.array([9.0, 5.0]), threshold=1.0, ft0=0
        )
        self.assertEqual(acts, [0, 1])
        self.assertEqual(boundaries, [0.0, 1.0, 1.0])
        self.assertEqual(total, 10.0)

    def test_bank_caps_at_five(self):
        _, acts, boundaries = sweep.play_season(
            np.ones(7), np.zeros(7), threshold=1.0
        )
        self.assertEqual(acts, [])
        self.assertEqual(boundaries, [1.0, 2.0, 3.0, 4.0, 5.0, 5.0, 5.0, 5.0])

    def test_vectorised_simulation_matches_reference_policy(self):
        gains, noise = self._draw(seed=91, seasons=8)
        thresholds = np.array([0.0, 1.25, 2.0, 6.0])
        sigma = 3.5
        actual = sweep.simulate_totals(gains, noise, sigma, thresholds=thresholds)

        expected = np.empty_like(actual)
        observations = gains + sigma * noise
        for threshold_index, threshold in enumerate(thresholds):
            for season in range(gains.shape[0]):
                expected[threshold_index, season] = sweep.play_season(
                    gains[season], observations[season], threshold
                )[0]
        np.testing.assert_array_equal(actual, expected)

    def test_zero_noise_optimum_is_zero_with_one_free_move_every_week(self):
        gains, noise = self._draw()
        means = sweep.simulate_totals(gains, noise, sigma=0.0).mean(axis=1)
        optimum = sweep.THRESHOLDS[int(np.argmax(means))]

        self.assertEqual(optimum, 0.0)

    def test_single_candidate_never_pays_hits_with_one_initial_free_transfer(self):
        gains, noise = self._draw(seasons=16)
        for sigma in (0.0, 2.3, 5.16):
            for tau in (0.0, 2.0, 6.0):
                expected = np.where(gains+sigma*noise >= tau, gains, 0.0).sum(axis=1)
                actual = sweep.simulate_totals(gains,noise,sigma,thresholds=[tau])[0]
                np.testing.assert_allclose(actual,expected,rtol=1e-14)


if __name__ == "__main__":
    unittest.main()
