import sys
import unittest
from pathlib import Path

import pulp


V2 = Path(__file__).resolve().parents[1] / "v2"
sys.path.insert(0, str(V2))
from planner import _valid_incumbent, describe  # noqa: E402
from weekly import _supersede_transfer_recommendation  # noqa: E402


class PlannerDescriptionTests(unittest.TestCase):
    def test_preseason_rebuild_supersedes_single_move_headline(self):
        lines = [
            "## Transfers",
            "**Recommended:** Destan → Obi (+1.6).",
            "## The next six weeks, planned",
        ]
        push = ["Fix unavailable: Destan→Obi +1.6", "Two-mover worth it: x"]
        transfers = {"advice": "Recommended: Destan → Obi (+1.6)."}
        replacement = (
            "**Recommended:** use the free pre-GW1 rebuild shown below "
            "(+5.9 versus holding/re-planning)."
        )

        _supersede_transfer_recommendation(
            lines, push, transfers, replacement,
            "Use the free pre-GW1 rebuild (+5.9)",
        )

        headlines = [line for line in lines if line.startswith("**Recommended:")]
        self.assertEqual(headlines, [replacement])
        self.assertEqual(transfers["advice"], replacement.replace("**", ""))
        self.assertEqual(push, ["Use the free pre-GW1 rebuild (+5.9)"])

    def test_incumbent_must_be_integral_and_satisfy_every_constraint(self):
        problem = pulp.LpProblem("incumbent")
        pick = problem.add_variable("pick", cat="Binary")
        problem += pick == 1

        self.assertFalse(_valid_incumbent(problem))
        pick.varValue = 0.5
        self.assertFalse(_valid_incumbent(problem))
        pick.varValue = 1.0
        self.assertTrue(_valid_incumbent(problem))

    def test_transfer_description_pairs_players_by_position(self):
        players = {
            1: {"name": "Old Mid", "pos": "MID"},
            2: {"name": "Old Def", "pos": "DEF"},
            3: {"name": "New Def", "pos": "DEF"},
            4: {"name": "New Mid", "pos": "MID"},
            5: {"name": "Captain", "pos": "FWD"},
        }
        result = {
            "weeks": [{
                "gw": 1, "pts": 70, "hits": 0, "captain": 5, "ft": 15,
                "in": [3, 4], "out": [1, 2],
            }]
        }

        line = describe(result, players)[0]

        self.assertIn("Old Def → New Def", line)
        self.assertIn("Old Mid → New Mid", line)
        self.assertNotIn("Old Mid → New Def", line)


if __name__ == "__main__":
    unittest.main()
