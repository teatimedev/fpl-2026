import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import simulate


ROOT = Path(__file__).resolve().parents[1]


class SimulationCliTests(unittest.TestCase):
    def test_rolling_window_keeps_blank_gameweeks_in_average(self):
        self.assertEqual(simulate.simulation_gameweeks(3, 5), [3, 4, 5])
        self.assertEqual(
            simulate.projection_window_average([9.0, 9.0, 6.0, 0.0], 3, 4),
            3.0,
        )

    def test_player_records_no_appearance_or_points_in_a_blank_gameweek(self):
        par = {
            "et": 4, "team": 1, "p_start_by_gw": [1.0, 1.0],
            "p_cameo_by_gw": [0.0, 0.0],
            "start_minutes_by_gw": [90.0, 90.0],
            "cameo_minutes_by_gw": [0.0, 0.0],
            "xg90": 0.0, "xa90": 0.0, "bonus90": 0.0,
            "saves90": 0.0, "yellow90": 0.0, "dc90": 0.0,
            "k_att": 1.0, "add": 0.0, "_played": [],
        }
        gf = {1: simulate.np.zeros((2, 2), dtype=int)}
        ga = {1: simulate.np.zeros((2, 2), dtype=int)}
        with patch.object(simulate, "START_GW", 3), \
                patch.object(simulate, "WINDOW", 2), \
                patch.object(simulate, "TEAM_SCHED", {1: {3: (1.0, 1.0)}}):
            points = simulate.simulate_player(par, gf, ga, 2)

        self.assertTrue((points[:, 1] == 0).all())
        self.assertFalse(par["_played"][1].any())

    def test_player_outcome_is_shared_between_compared_squads(self):
        simulate._OUTCOME_CACHE.clear()
        simulate._ADD_CACHE.clear()
        gf, ga = simulate.simulate_teams(2)
        player_id = next(iter(simulate.V2_PLAYERS))

        first = simulate.player_outcome(player_id, gf, ga, 2)
        second = simulate.player_outcome(player_id, gf, ga, 2)

        self.assertIs(first[1], second[1])
        self.assertIs(first[2], second[2])

    def test_small_simulation_completes(self):
        result = subprocess.run(
            [sys.executable, "simulate.py", "--sims", "2"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 simulations", result.stdout)
        self.assertIn("auto-subs", result.stdout)


if __name__ == "__main__":
    unittest.main()
