import unittest

from v2.scorecard import grade, started_outcome


class NewsScorecardTests(unittest.TestCase):
    def test_double_gameweek_start_count_is_boolean_target(self):
        self.assertEqual(started_outcome([10, 180, 2]), 1)
    def test_scores_start_appearance_and_minutes_forecasts(self):
        snap = {
            "gw": 2, "generated": "x", "team_cs": {}, "squad": [],
            "players": [
                {"id": 1, "name": "One", "team": "ARS", "pos": "MID", "proj": 4,
                 "start_rate": .8, "status": "a", "p_start": .8, "p_play": .9, "expected_minutes": 70},
                {"id": 2, "name": "Two", "team": "ARS", "pos": "DEF", "proj": 2,
                 "start_rate": .2, "status": "a", "p_start": .2, "p_play": .5, "expected_minutes": 20},
            ],
        }
        actual = {"points": {"1": [6, 90, 1], "2": [1, 10, 0]}, "cs": {}}
        scored = grade(snap, actual)["availability"]
        self.assertEqual(scored["n"], 2)
        self.assertAlmostEqual(scored["start_brier"], .04)
        self.assertAlmostEqual(scored["appearance_brier"], .13)
        self.assertAlmostEqual(scored["minutes_mae"], 15)
        self.assertAlmostEqual(scored["minutes_bias"], 5)

    def test_compares_deadline_start_forecast_with_historical_baseline(self):
        snap = {
            "gw": 2, "generated": "x", "team_cs": {}, "squad": [],
            "players": [{
                "id": 1, "name": "One", "team": "ARS", "pos": "MID", "proj": 4,
                "start_rate": .8, "status": "a", "p_start": .1, "p_play": .2,
                "expected_minutes": 5, "baseline_start": .8,
                "availability_source": "https://www.arsenal.com/news", "availability_confidence": "high",
                "generation_rule": "explicit_absence_v1",
            }],
        }
        actual = {"points": {"1": [0, 0, 0]}, "cs": {}}
        result = grade(snap, actual)
        self.assertGreater(result["availability"]["start_brier_lift"], 0)
        self.assertIn("explicit_absence_v1", result["availability_groups"]["claim_type"])


if __name__ == "__main__":
    unittest.main()
