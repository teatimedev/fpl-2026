import unittest

from v2.squad_evaluator import (
    apply_autosubs,
    captain_replacement,
    deadline_unavailable,
    evaluate_squad,
    pick_lineup,
)


def player(pid, pos, points, play=1.0):
    return {
        "id": pid,
        "name": str(pid),
        "pos": pos,
        "proj_by_gw": [points],
        "play_by_gw": [play],
        "start_rate": play,
        "status": "a" if play else "u",
    }


class SquadRuleTests(unittest.TestCase):
    def setUp(self):
        self.squad = [
            player("g1", "GKP", 5),
            player("g2", "GKP", 4),
            player("d1", "DEF", 6),
            player("d2", "DEF", 5),
            player("d3", "DEF", 4),
            player("d4", "DEF", 1),
            player("d5", "DEF", 0),
            player("m1", "MID", 10),
            player("m2", "MID", 9),
            player("m3", "MID", 8),
            player("m4", "MID", 7),
            player("m5", "MID", 2),
            player("f1", "FWD", 8),
            player("f2", "FWD", 7),
            player("f3", "FWD", 3),
        ]

    def test_pick_lineup_is_legal_and_goalkeeper_is_separate_on_bench(self):
        lineup = pick_lineup(self.squad, 1)

        counts = {pos: sum(p["pos"] == pos for p in lineup.xi)
                  for pos in ("GKP", "DEF", "MID", "FWD")}
        self.assertEqual(len(lineup.xi), 11)
        self.assertEqual(counts, {"GKP": 1, "DEF": 3, "MID": 4, "FWD": 3})
        self.assertEqual(lineup.bench[0]["pos"], "GKP")

    def test_autosub_skips_first_midfielder_when_three_defender_minimum_would_break(self):
        xi = [
            player("g1", "GKP", 5),
            player("d1", "DEF", 6), player("d2", "DEF", 5),
            player("d3", "DEF", 4),
            player("m1", "MID", 10), player("m2", "MID", 9),
            player("m3", "MID", 8), player("m4", "MID", 7),
            player("f1", "FWD", 8), player("f2", "FWD", 7),
            player("f3", "FWD", 3),
        ]
        bench = [player("g2", "GKP", 4), player("m5", "MID", 2),
                 player("d4", "DEF", 1), player("d5", "DEF", 0)]
        played = {p["id"]: True for p in xi + bench}
        played["d1"] = False

        result = apply_autosubs(xi, bench, played)

        self.assertIn(("d1", "d4"), result.substitutions)
        self.assertNotIn("m5", result.scoring_ids)

    def test_goalkeeper_can_only_be_replaced_by_bench_goalkeeper(self):
        lineup = pick_lineup(self.squad, 1)
        played = {p["id"]: True for p in self.squad}
        played[lineup.xi[0]["id"]] = False
        bench_keeper = next(p for p in lineup.bench if p["pos"] == "GKP")
        played[bench_keeper["id"]] = False

        result = apply_autosubs(lineup.xi, lineup.bench, played)

        self.assertFalse(any(out_id == lineup.xi[0]["id"]
                             for out_id, _ in result.substitutions))

    def test_captaincy_falls_to_vice_but_never_to_a_third_player(self):
        played = {"captain": False, "vice": False, "third": True}

        self.assertIsNone(captain_replacement("captain", "vice", played))
        played["vice"] = True
        self.assertEqual(captain_replacement("captain", "vice", played), "vice")


class SquadValueTests(unittest.TestCase):
    def test_deadline_warning_uses_current_gameweek_not_whole_horizon(self):
        risky = player("risky", "MID", 5, 0)
        risky["status"] = "a"
        risky["play_by_gw"] = [0.0, 1.0]
        risky["proj_by_gw"] = [0.0, 5.0]

        self.assertEqual(deadline_unavailable([risky], 1), [risky])
        self.assertEqual(deadline_unavailable([risky], 2), [])

    def test_second_sub_can_enter_when_first_sub_does_not_play(self):
        squad = [
            player("g1", "GKP", 5, 1), player("g2", "GKP", 0, 0),
            player("d1", "DEF", 10, 0),
            *[player(f"d{i}", "DEF", 5, 1) for i in range(2, 6)],
            *[player(f"m{i}", "MID", 6, 1) for i in range(1, 6)],
            *[player(f"f{i}", "FWD", 5, 1) for i in range(1, 4)],
        ]
        # The first two outfield reserves have 4 and 3 unconditional points.
        # If the first appears only half the time, the second covers the same
        # certain starter absence in the other half of scenarios.
        next(p for p in squad if p["id"] == "f2")["proj_by_gw"] = [4.0]
        next(p for p in squad if p["id"] == "f2")["play_by_gw"] = [0.5]
        next(p for p in squad if p["id"] == "d5")["proj_by_gw"] = [3.0]
        next(p for p in squad if p["id"] == "f3")["proj_by_gw"] = [2.0]

        evaluation = evaluate_squad(squad, 1, 1)

        self.assertGreaterEqual(evaluation.autosub_points, 5.5)

    def test_lineup_is_reselected_each_gameweek(self):
        squad = [
            player("g1", "GKP", 5), player("g2", "GKP", 4),
            *[player(f"d{i}", "DEF", 5) for i in range(1, 6)],
            *[player(f"m{i}", "MID", 6) for i in range(1, 6)],
            *[player(f"f{i}", "FWD", 5) for i in range(1, 4)],
        ]
        for p in squad:
            p["proj_by_gw"] = [p["proj_by_gw"][0], p["proj_by_gw"][0]]
            p["play_by_gw"] = [1.0, 1.0]
        next(p for p in squad if p["id"] == "d5")["proj_by_gw"] = [0.0, 10.0]
        next(p for p in squad if p["id"] == "m5")["proj_by_gw"] = [10.0, 0.0]

        evaluation = evaluate_squad(squad, 1, 2)
        first = {p["id"] for p in evaluation.weeks[0].lineup.xi}
        second = {p["id"] for p in evaluation.weeks[1].lineup.xi}

        self.assertIn("m5", first)
        self.assertNotIn("d5", first)
        self.assertIn("d5", second)
        self.assertNotIn("m5", second)

    def test_bench_value_responds_to_starter_dnp_risk_and_bench_availability(self):
        base = [
            player("g1", "GKP", 5, 1), player("g2", "GKP", 4, 1),
            *[player(f"d{i}", "DEF", 5, 1) for i in range(1, 6)],
            *[player(f"m{i}", "MID", 6, 1) for i in range(1, 6)],
            *[player(f"f{i}", "FWD", 5, 1) for i in range(1, 4)],
        ]
        safe = evaluate_squad(base, 1, 1)

        risky = [dict(p) for p in base]
        next(p for p in risky if p["id"] == "m1")["play_by_gw"] = [0.5]
        live_cover = evaluate_squad(risky, 1, 1)

        dead_cover = [dict(p) for p in risky]
        bench_mid = min((p for p in dead_cover if p["pos"] == "MID"),
                        key=lambda p: p["proj_by_gw"][0])
        bench_mid["proj_by_gw"] = [0.0]
        bench_mid["play_by_gw"] = [0.0]
        no_cover = evaluate_squad(dead_cover, 1, 1)

        self.assertEqual(safe.autosub_points, 0.0)
        self.assertGreater(live_cover.autosub_points, 0.0)
        self.assertLess(no_cover.autosub_points, live_cover.autosub_points)


if __name__ == "__main__":
    unittest.main()
