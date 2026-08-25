"""P1: per-gameweek player rows persist from the element-summary `history`
array into gw_stat, and the consistency checks that guard the table."""
import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from v2.fetch import (
    GW_STAT_COLUMNS, GW_STAT_INSERT, check_gw_stats, export_gw_stats,
    gw_rows_for, schema,
)


TEAM_SHORT = {1: "ARS", 2: "MCI", 3: "BOU"}


def history_row(**kw):
    row = dict(element=411, fixture=7, opponent_team=3, total_points=2,
               was_home=True, kickoff_time="2026-08-23T15:30:00Z", round=1,
               minutes=90, goals_scored=0, assists=0, clean_sheets=0,
               goals_conceded=1, own_goals=0, penalties_saved=0,
               penalties_missed=0, yellow_cards=0, red_cards=0, saves=0,
               bonus=0, bps=12, influence="20.4", creativity="3.1",
               threat="45.0", defensive_contribution=1, starts=1,
               expected_goals="0.81", expected_assists="0.05",
               expected_goals_conceded="1.20", value=155, selected=6_900_000)
    row.update(kw)
    return row


class GwRowsTests(unittest.TestCase):
    def test_history_rows_map_to_gw_stat_columns(self):
        res = {"history": [history_row(), history_row(fixture=15, round=2,
                                                       minutes=0, starts=0,
                                                       total_points=0)]}
        rows = gw_rows_for(4001, "MCI", "FWD",res, TEAM_SHORT)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(len(r) == len(GW_STAT_COLUMNS) for r in rows))
        first = dict(zip(GW_STAT_COLUMNS, rows[0]))
        self.assertEqual(first["code"], 4001)
        self.assertEqual(first["season"], "2026/27")
        self.assertEqual(first["team"], "MCI")
        self.assertEqual(first["pos"], "FWD")
        self.assertEqual(first["opponent"], "BOU")
        self.assertEqual(first["was_home"], 1)
        self.assertEqual(first["minutes"], 90)
        self.assertEqual(first["starts"], 1)
        self.assertAlmostEqual(first["xg"], 0.81)
        self.assertAlmostEqual(first["threat"], 45.0)
        self.assertEqual(first["price"], 155)
        # the non-appearance row is kept, with zero minutes and zero starts
        second = dict(zip(GW_STAT_COLUMNS, rows[1]))
        self.assertEqual((second["minutes"], second["starts"]), (0, 0))

    def test_missing_history_yields_no_rows(self):
        self.assertEqual(gw_rows_for(1, "ARS", "MID",{}, TEAM_SHORT), [])
        self.assertEqual(gw_rows_for(1, "ARS", "MID",{"history": None}, TEAM_SHORT), [])


class ConsistencyTests(unittest.TestCase):
    def _db(self, rows):
        cx = sqlite3.connect(":memory:")
        schema(cx)
        cx.executemany(GW_STAT_INSERT, rows)
        return cx

    def _boot(self, live=True, **totals):
        element = dict(id=1, code=4001, web_name="Haaland", team=2, minutes=90,
                       total_points=2, starts=1)
        element.update(totals)
        return {"events": [{"id": 1, "is_current": live, "finished": live}],
                "elements": [element], "teams": [{"id": k, "short_name": v}
                                                 for k, v in TEAM_SHORT.items()]}

    def test_sums_match_bootstrap_totals(self):
        rows = gw_rows_for(4001, "MCI", "FWD",{"history": [history_row()]}, TEAM_SHORT)
        cx = self._db(rows)
        fixtures = [dict(id=7, finished=True, team_h=2, team_a=3, team_h_score=1)]
        self.assertEqual(check_gw_stats(cx, self._boot(), fixtures), [])

    def test_mismatch_and_extra_rows_are_reported(self):
        rows = gw_rows_for(4001, "MCI", "FWD",{"history": [history_row(), history_row(fixture=9)]},
                           TEAM_SHORT)
        cx = self._db(rows)
        fixtures = [dict(id=7, finished=True, team_h=2, team_a=3, team_h_score=1)]
        problems = check_gw_stats(cx, self._boot(), fixtures)
        self.assertTrue(any("gw_stat sums" in p for p in problems))
        self.assertTrue(any("has played 1 fixture" in p for p in problems))

    def test_player_with_minutes_but_no_rows_is_reported(self):
        cx = self._db([])
        problems = check_gw_stats(cx, self._boot(), [])
        self.assertEqual(len(problems), 1)
        self.assertIn("no gw_stat rows", problems[0])

    def test_pre_season_bootstrap_is_not_checked(self):
        cx = self._db([])
        self.assertEqual(check_gw_stats(cx, self._boot(live=False), []), [])

    def test_csv_export_round_trips_every_column(self):
        rows = gw_rows_for(4001, "MCI", "FWD",{"history": [history_row()]}, TEAM_SHORT)
        cx = self._db(rows)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gw_stats.csv"
            n = export_gw_stats(cx, path)
            with open(path, newline="") as fh:
                got = list(csv.DictReader(fh))
        self.assertEqual(n, 1)
        self.assertEqual(list(got[0]), list(GW_STAT_COLUMNS))
        self.assertEqual(got[0]["opponent"], "BOU")
        self.assertEqual(got[0]["minutes"], "90")


if __name__ == "__main__":
    unittest.main()
