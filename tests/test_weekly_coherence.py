import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "v2"))
import weekly  # noqa: E402


def _weekly():
    return json.loads((ROOT / "data" / "weekly.json").read_text())


def _player_ids_by_name():
    data = json.loads((ROOT / "app" / "src" / "data" / "fpl.json").read_text())
    return {player["name"]: player["id"] for player in data["players"]}


class WeeklyCoherenceTests(unittest.TestCase):
    def test_lineup_and_bench_are_one_complete_squad(self):
        squad = _weekly()["squad"]
        lineup = squad["lineup"]

        self.assertEqual(len(lineup["xi"]), 11)
        self.assertEqual(len(lineup["bench"]), 4)
        self.assertEqual(set(lineup["xi"]), set(squad["ids"]) - set(lineup["bench"]))
        self.assertFalse(set(lineup["xi"]) & set(lineup["bench"]))

    def test_weekly_snapshot_records_when_the_private_squad_was_confirmed(self):
        squad = _weekly()["squad"]

        self.assertEqual(squad["source"], "confirmed pre-deadline squad")
        self.assertTrue(squad["confirmed_at"])
        self.assertTrue(squad["entry_id"])
        self.assertTrue(squad["changes"])

    def test_recorded_sync_changes_are_reflected_in_the_squad_and_lineup(self):
        squad = _weekly()["squad"]
        ids = set(squad["ids"])
        names = _player_ids_by_name()

        for change in squad.get("changes", []):
            if " → " in change:
                outgoing, incoming = change.split(" → ", 1)
                self.assertNotIn(names[outgoing], ids)
                self.assertIn(names[incoming], ids)
            if ";" in change and " starts" in change:
                starter, substitute = change.split(";", 1)
                starter = starter.removesuffix(" starts")
                substitute = substitute.strip().split(" is ", 1)[0]
                self.assertIn(names[starter], squad["lineup"]["xi"])
                self.assertIn(names[substitute], squad["lineup"]["bench"])

    def test_web_bundle_contains_the_current_weekly_snapshot(self):
        weekly_data = _weekly()
        weekly_data.pop("digest_md", None)
        app_data = json.loads(
            (ROOT / "app" / "src" / "data" / "fpl.json").read_text()
        )

        self.assertEqual(app_data["weekly"], weekly_data)

    def test_structured_decision_and_displayed_instruction_agree(self):
        weekly_data = _weekly()

        self.assertEqual(weekly_data["decision"]["kind"], "hold")
        self.assertEqual(
            weekly_data["decision"]["instruction"],
            weekly_data["transfers"]["advice"],
        )


# ------------------------------------------------------------ retro (P3)
def _player(pid, pos, team, pts, price, play=0.95):
    return dict(id=pid, name=f"P{pid}", full_name=f"Player {pid}", team=team, pos=pos,
                price=price, status="a", proj_by_gw=[pts, pts], play_by_gw=[play, play],
                start_by_gw=[play, play], start_rate=play, sel_pct=1.0, news="",
                joined="2020-07-01", availability_by_gw=[None, None])


def _players():
    """A 15-man squad plus a small pool, so transfer_engine runs in seconds."""
    squad = [
        _player(1, "GKP", "ARS", 4.0, 5.0), _player(2, "GKP", "BOU", 3.0, 4.0),
        _player(3, "DEF", "ARS", 5.0, 6.0), _player(4, "DEF", "BOU", 4.5, 5.0),
        _player(5, "DEF", "CHE", 4.0, 4.5), _player(6, "DEF", "CRY", 3.5, 4.5),
        _player(7, "DEF", "EVE", 3.0, 4.0),
        _player(8, "MID", "ARS", 7.0, 9.5), _player(9, "MID", "BOU", 6.0, 8.0),
        _player(10, "MID", "CHE", 5.5, 7.0), _player(11, "MID", "CRY", 5.0, 6.0),
        _player(12, "MID", "EVE", 2.5, 4.5),
        _player(13, "FWD", "MCI", 8.0, 15.5), _player(14, "FWD", "BRE", 5.0, 8.0),
        _player(15, "FWD", "EVE", 2.0, 4.5),
    ]
    pool = [
        _player(21, "FWD", "LIV", 6.0, 8.0), _player(22, "FWD", "TOT", 4.0, 6.0),
        _player(23, "MID", "LIV", 6.5, 8.0), _player(24, "MID", "TOT", 5.5, 6.5),
        _player(25, "DEF", "LIV", 5.0, 5.5), _player(26, "DEF", "TOT", 4.5, 5.0),
        _player(27, "GKP", "LIV", 4.5, 5.0),
    ]
    return {p["id"]: p for p in squad + pool}, [p["id"] for p in squad]


def _retro(ids):
    comps = dict(minutes=-5.0, chance=0.0, finishing=0.0, team=0.0, defcon=0.0,
                 bonus=0.0, other=0.0, unexplained=0.0)
    var = dict(minutes=0.3, chance=0.1, finishing=-4.2, team=0.0, defcon=0.0,
               bonus=-0.5, other=0.0, unexplained=0.0)
    return dict(gw=1, n_players=4, counts={"minutes_loss": 2, "variance": 1, "minutes_gain": 1},
                players=[
                    dict(id=14, name="P14", team="BRE", pos="FWD", proj=5.0, actual=0,
                         minutes=0, starts=0, xg=0.0, xa=0.0, goals=0, assists=0,
                         p_start=0.97, status="a", components=comps, **{"class": "minutes_loss"},
                         subtype="dnp", tags=[], streak=1, proj_next=4.9, next_gw=2,
                         start_move="start estimate 97% -> 78%",
                         note="0 minutes, healthy (status a; deadline start estimate 97%)"),
                    dict(id=13, name="P13", team="MCI", pos="FWD", proj=7.3, actual=2,
                         minutes=90, starts=1, xg=0.8, xa=0.1, goals=0, assists=0,
                         p_start=0.92, status="a", components=var, **{"class": "variance"},
                         subtype="finishing", tags=["blanked_good_xg"], streak=0,
                         proj_next=7.5, next_gw=2, start_move="start estimate 84% -> 87%",
                         note="90', 0.80 xG, 0 goals, 2 pts (proj 7.3). Finishing (-4.2)."),
                    dict(id=21, name="P21", team="LIV", pos="FWD", proj=1.0, actual=9,
                         minutes=90, starts=1, xg=0.3, xa=0.1, goals=1, assists=1,
                         p_start=0.3, status="a", components=var, **{"class": "minutes_gain"},
                         subtype=None, tags=["breakout_minutes"], streak=0, proj_next=6.0,
                         next_gw=2, start_move=None, note="started at a 30% deadline estimate, 90'"),
                    dict(id=23, name="P23", team="LIV", pos="MID", proj=5.0, actual=0,
                         minutes=0, starts=0, xg=0.0, xa=0.0, goals=0, assists=0,
                         p_start=0.9, status="a", components=comps, **{"class": "minutes_loss"},
                         subtype="dnp", tags=[], streak=2, proj_next=3.0, next_gw=2,
                         start_move=None, note="0 minutes, healthy"),
                ])


class RetroCoherenceTests(unittest.TestCase):
    """The retrospective changes ordering and wording, never numbers."""

    def setUp(self):
        self.players, self.ids = _players()
        self.squad = [self.players[i] for i in self.ids]
        self.gw, self.horizon = 2, 2

    def _engine(self):
        return weekly.transfer_engine(self.squad, self.players, 0.0, 1, self.gw,
                                      self.horizon, pool_size=4)

    def test_transfer_numbers_are_bit_identical_with_and_without_the_retro(self):
        eng = self._engine()
        before = copy.deepcopy(eng)
        squad_before = copy.deepcopy(self.squad)
        players_before = copy.deepcopy(self.players)
        retro = _retro(self.ids)
        retro_before = copy.deepcopy(retro)

        lines, push, J = weekly.retro_review_lines(retro, self.players, self.gw, self.horizon,
                                                   set(self.ids), graded_gws=0)
        warnings = weekly.retro_minutes_warnings(retro, self.squad, eng)
        notes = weekly.retro_table_notes(retro, eng)
        verdict = weekly.retro_verdict_lines(retro, self.squad)
        flags = weekly.retro_check_flags(retro, set(self.ids))

        self.assertEqual(eng, before)
        self.assertEqual(self.squad, squad_before)
        self.assertEqual(self.players, players_before)
        self.assertEqual(retro, retro_before)
        # and the transfer engine, re-run, gives the same numbers
        again = self._engine()
        self.assertEqual(again["base"], before["base"])
        self.assertEqual([(s["out"]["id"], s["in_"]["id"], s["gain"], s["net"])
                          for s in again["singles"]],
                         [(s["out"]["id"], s["in_"]["id"], s["gain"], s["net"])
                          for s in before["singles"]])
        self.assertTrue(lines and warnings and verdict and flags == {})
        self.assertIsInstance(notes, list)
        self.assertTrue(push.startswith("Last GW: "))

    def test_review_lines_never_carry_a_verdict_or_the_word_form(self):
        retro = _retro(self.ids)
        eng = self._engine()
        lines, push, J = weekly.retro_review_lines(retro, self.players, self.gw, self.horizon,
                                                   set(self.ids))
        everything = lines + weekly.retro_minutes_warnings(retro, self.squad, eng) \
            + weekly.retro_table_notes(retro, eng) + weekly.retro_verdict_lines(retro, self.squad)
        for line in everything:
            self.assertFalse(line.startswith("**Recommended"), line)
            self.assertFalse(line.startswith("**No single"), line)
            self.assertNotIn("form", line.lower().replace("formation", ""), line)
        # the advice extraction main() uses is unaffected by the extra lines
        L = ["**Recommended: hold.** The best free move is only +1.2 over the window."]
        L += weekly.retro_verdict_lines(retro, self.squad)
        advice = next(l for l in reversed(L) if l.startswith("**Recommended")
                      or l.startswith("**No single"))
        self.assertEqual(advice, L[0])
        self.assertIn("check-first", L[-1])

    def test_review_orders_owned_minutes_cases_first_and_pool_by_projection(self):
        retro = _retro(self.ids)
        lines, push, J = weekly.retro_review_lines(retro, self.players, self.gw, self.horizon,
                                                   set(self.ids), graded_gws=3)
        self.assertEqual(lines[0], "## GW1 in review — what happened, and what it does and does not change")
        self.assertIn("graded: 3 weeks", lines[2])
        self.assertEqual(J["act"], [14])
        self.assertEqual(J["hold"], [13])
        # pool: the owned player 14 is excluded; the pool minutes_loss (23) and
        # breakout (21) appear under their lists, with the projection window
        self.assertEqual(J["pool"]["breakout minutes (started at <= 40%)"], [21])
        self.assertEqual(J["pool"]["lost their place — benched while healthy"], [23])
        self.assertTrue(any("0.80 xG" in l for l in lines))          # a blank prints its xG
        self.assertTrue(any("97% -> 78%" in l for l in lines))        # direction of the move
        self.assertEqual(push, "Last GW: P14 benched (healthy) — check · P13 blank = variance, hold")
        table = [l for l in lines if l.startswith("| P")]
        self.assertEqual(len(table), 2)
        self.assertTrue(table[0].startswith("| P13") or table[0].startswith("| P14"))

    def test_minutes_warning_names_a_replacement_even_under_the_hold_threshold(self):
        retro = _retro(self.ids)
        eng = self._engine()
        warnings = weekly.retro_minutes_warnings(retro, self.squad, eng)
        self.assertTrue(warnings[0].startswith("**Minutes warning:** P14"))
        self.assertIn("check first", warnings[0])
        move = next((s for s in eng["all_singles"] if s["out"]["id"] == 14), None)
        if move:
            self.assertIn(move["in_"]["name"], warnings[0])
            if move["gain"] < weekly.HOLD_THRESHOLD:
                self.assertIn("under the hold threshold", warnings[0])

    def test_check_flags_cover_role_change_and_minutes_watch_only(self):
        retro = _retro(self.ids)
        retro["players"][1]["class"] = "minutes_watch"
        retro["players"][1]["subtype"] = "hooked"
        retro["players"][1]["note"] = "started, hooked on 58'"
        flags = weekly.retro_check_flags(retro, set(self.ids))
        self.assertEqual(set(flags), {13})
        self.assertIn("hooked on 58'", flags[13])
        for text in flags.values():
            self.assertNotIn("chance", text)
            self.assertNotIn("status", text)

    def test_load_retro_returns_none_when_absent_or_empty(self):
        self.assertIsNone(weekly.load_retro(0))
        self.assertIsNone(weekly.load_retro(999))


if __name__ == "__main__":
    unittest.main()
