"""P3: the retrospective's decomposition identity and fixture-driven
classification (the three §1.8 players and the precedence cases)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "v2"))

import retro  # noqa: E402


# a league-average fixture (xg 1.5 -> volume ~1.0) so the chance-quality piece
# of a par-xG match sits inside the band and the finishing piece carries it
FIXTURE = [dict(opp="BOU", home=True, xg=1.5, xgc=0.9, cs=0.42)]


def snap_row(**kw):
    row = dict(id=411, name="Haaland", team="MCI", pos="FWD", price=15.5, proj=5.0,
               start_rate=0.92, status="a", p_start=0.92, p_play=0.956,
               expected_minutes=70.8, baseline_start=0.84, p_cameo=0.45,
               start_minutes=76, cameo_minutes=25, xg90=0.787, xa90=0.076,
               dc90=1.5, bonus90=0.5, saves90=0.0, yellow90=0.1, evidence=0.96,
               dc_evidence=0.9, k=1.0, pens=1, corners=None, fk=None,
               availability_source="model baseline")
    row.update(kw)
    return row


def stats(**kw):
    s = dict(minutes=90, starts=1, goals_scored=0, assists=0, clean_sheets=0,
             goals_conceded=1, own_goals=0, penalties_saved=0, penalties_missed=0,
             yellow_cards=0, red_cards=0, saves=0, bonus=0, bps=10,
             defensive_contribution=2, expected_goals=0.8, expected_assists=0.1,
             expected_goals_conceded=1.0, total_points=2)
    s.update(kw)
    return s


def explain_for(s, pos="FWD"):
    """FPL-style per-stat points breakdown consistent with `s`; also sets
    s['total_points'] to the sum, as the API does."""
    gp = retro.GOAL_PTS[pos]
    items = []
    m = s["minutes"]
    items.append(("minutes", 2 if m >= 60 else (1 if m > 0 else 0), m))
    if s["goals_scored"]:
        items.append(("goals_scored", s["goals_scored"] * gp, s["goals_scored"]))
    if s["assists"]:
        items.append(("assists", s["assists"] * 3, s["assists"]))
    if s["clean_sheets"] and m >= 60 and retro.CS_PTS[pos]:
        items.append(("clean_sheets", retro.CS_PTS[pos], 1))
    if pos in ("GKP", "DEF") and s["goals_conceded"] >= 2:
        items.append(("goals_conceded", -(s["goals_conceded"] // 2), s["goals_conceded"]))
    if s["bonus"]:
        items.append(("bonus", s["bonus"], s["bonus"]))
    if s["yellow_cards"]:
        items.append(("yellow_cards", -1, 1))
    if s["red_cards"]:
        items.append(("red_cards", -3, 1))
    thr = retro.DC_THRESHOLD[pos]
    if thr and s["defensive_contribution"] >= thr:
        items.append(("defensive_contribution", 2, s["defensive_contribution"]))
    s["total_points"] = sum(p for _, p, _ in items)
    return [dict(fixture=1, stats=[dict(identifier=i, points=p, value=v) for i, p, v in items])]


NAMED = ("minutes", "chance", "finishing", "team", "defcon", "bonus", "other")


class DecompositionIdentity(unittest.TestCase):
    def _check(self, row, s, pos="FWD"):
        ex = explain_for(s, pos)
        comps, proj_recon, actual = retro.decompose(row, FIXTURE, s, ex)
        self.assertAlmostEqual(sum(comps[c] for c in NAMED), actual - row["proj"], places=2)
        self.assertAlmostEqual(comps["unexplained"], 0.0, places=2)
        return comps, proj_recon, actual

    def test_pieces_sum_to_actual_minus_proj_for_a_blank(self):
        comps, proj_recon, actual = self._check(snap_row(), stats())
        self.assertEqual(actual, 2)
        # the snapshot's proj is reconstructed from its own rates to within noise
        self.assertLess(abs(proj_recon - 5.0), 1.5)
        self.assertLess(comps["finishing"], -2.5)          # 0.8 xG, no goal
        self.assertLess(abs(comps["chance"]), 1.0)          # xG was about par
        self.assertGreater(comps["minutes"], 0.0)           # he started and played 90

    def test_identity_holds_for_a_non_appearance(self):
        comps, _, actual = self._check(snap_row(id=106, name="Thiago", proj=5.24,
                                                p_start=0.97, expected_minutes=88.7),
                                       stats(minutes=0, starts=0, expected_goals=0.0,
                                             expected_assists=0.0, defensive_contribution=0))
        self.assertEqual(actual, 0)
        self.assertLess(comps["minutes"], -4.5)             # the whole projection
        self.assertAlmostEqual(comps["finishing"], 0.0, places=6)
        self.assertAlmostEqual(comps["chance"], 0.0, places=6)

    def test_identity_holds_for_a_defender_haul(self):
        row = snap_row(id=4, name="Gabriel", pos="DEF", proj=6.05, xg90=0.15, xa90=0.05,
                       dc90=8.5, bonus90=0.6, p_start=0.83, start_minutes=90,
                       expected_minutes=74.4)
        s = stats(goals_scored=1, clean_sheets=1, goals_conceded=0, bonus=3,
                  defensive_contribution=11, expected_goals=0.3, expected_assists=0.1)
        comps, _, actual = self._check(row, s, "DEF")
        self.assertEqual(actual, 2 + 6 + 4 + 3 + 2)
        self.assertGreater(comps["finishing"], 3.0)
        self.assertGreater(comps["team"], 1.0)
        self.assertGreater(comps["bonus"], 1.0)

    def test_falls_back_to_stats_when_explain_is_missing(self):
        s = stats(goals_scored=1, total_points=6)
        comps, _, actual = retro.decompose(snap_row(), FIXTURE, s, None)
        self.assertAlmostEqual(sum(comps[c] for c in NAMED), actual - 5.0, places=2)
        self.assertAlmostEqual(comps["unexplained"], 0.0, places=2)

    def test_blank_gameweek_has_no_expected_points(self):
        comps, proj_recon, _ = retro.decompose(
            snap_row(proj=0.0), [], stats(minutes=0, starts=0, total_points=0), None)
        self.assertEqual(proj_recon, 0.0)
        self.assertAlmostEqual(comps["unexplained"], 0.0, places=6)


class Classification(unittest.TestCase):
    DEADLINE = "2026-08-21T17:30:00Z"

    def _classify(self, row, s, now=None, gw_rows=None, gw=1):
        ex = explain_for(s, row["pos"])
        comps, _, _ = retro.decompose(row, FIXTURE, s, ex)
        world = dict(status="a", news="", news_added=None, pens=row.get("pens"),
                     corners=row.get("corners"), fk=row.get("fk"))
        world.update(now or {})
        return retro.classify(row, s, world, comps, self.DEADLINE, gw_rows or [], gw)

    def test_haaland_blank_is_variance(self):
        cls, sub, tags, note = self._classify(snap_row(), stats())
        self.assertEqual(cls, "variance")
        self.assertEqual(sub, "finishing")
        self.assertIn("blanked_good_xg", tags)
        self.assertIn("0.80 xG", note)

    def test_thiago_zero_minutes_is_minutes_loss_dnp(self):
        row = snap_row(id=106, name="Thiago", proj=5.24, p_start=0.97)
        cls, sub, _, note = self._classify(row, stats(minutes=0, starts=0, expected_goals=0.0,
                                                       expected_assists=0.0,
                                                       defensive_contribution=0))
        self.assertEqual((cls, sub), ("minutes_loss", "dnp"))
        self.assertIn("97%", note)

    def test_foden_cameo_is_minutes_loss_cameo_and_start_is_minutes_watch(self):
        row = snap_row(id=398, name="Foden", pos="MID", proj=5.06, p_start=0.8,
                       xg90=0.286, xa90=0.232, expected_minutes=54)
        cameo = stats(minutes=15, starts=0, expected_goals=0.05, expected_assists=0.02,
                      defensive_contribution=1)
        self.assertEqual(self._classify(row, cameo)[:2], ("minutes_loss", "cameo"))
        hooked = stats(minutes=55, starts=1, expected_goals=0.2, expected_assists=0.1,
                       defensive_contribution=3)
        self.assertEqual(self._classify(row, hooked)[:2], ("minutes_watch", "hooked"))

    def test_injured_non_appearance_is_unavailable_not_minutes_loss(self):
        row = snap_row(p_start=0.05, status="i")
        cls, _, _, _ = self._classify(row, stats(minutes=0, starts=0))
        self.assertEqual(cls, "unavailable")
        # healthy at the deadline, but flagged since (injured in the match)
        cls, _, _, note = self._classify(
            snap_row(), stats(minutes=0, starts=0),
            now=dict(status="i", news_added="2026-08-23T18:00:00Z"))
        self.assertEqual(cls, "unavailable")
        self.assertIn("status i now", note)

    def test_override_to_zero_is_unavailable(self):
        row = snap_row(p_start=0.0, availability_source="official club news")
        self.assertEqual(self._classify(row, stats(minutes=0, starts=0))[0], "unavailable")

    def test_red_card_is_unavailable_before_anything_else(self):
        cls, _, _, note = self._classify(snap_row(), stats(red_cards=1))
        self.assertEqual((cls, note), ("unavailable", "red card"))

    def test_penalty_order_change_is_role_change(self):
        row = snap_row(pens=2)
        cls, sub, tags, note = self._classify(row, stats(), now=dict(pens=1))
        self.assertEqual((cls, sub), ("role_change", "setpiece"))
        self.assertIn("setpiece_change", tags)
        self.assertIn("first on penalties", note)

    def test_precedence_minutes_loss_beats_setpiece_change(self):
        row = snap_row(pens=2, p_start=0.9)
        cls, _, _, _ = self._classify(row, stats(minutes=0, starts=0), now=dict(pens=1))
        self.assertEqual(cls, "minutes_loss")

    def test_breakout_minutes(self):
        row = snap_row(id=9, name="Fringe", p_start=0.3, expected_minutes=30)
        cls, _, tags, _ = self._classify(row, stats())
        self.assertEqual(cls, "minutes_gain")
        self.assertIn("breakout_minutes", tags)

    def test_fringe_non_start_is_minutes_watch(self):
        row = snap_row(id=9, name="Fringe", p_start=0.45, expected_minutes=45)
        cls, sub, _, _ = self._classify(row, stats(minutes=0, starts=0))
        self.assertEqual((cls, sub), ("minutes_watch", "fringe"))

    def test_on_model_when_residual_is_small(self):
        s = stats(goals_scored=1, expected_goals=0.9, expected_assists=0.1)   # 6 pts vs 5.0
        cls, _, tags, _ = self._classify(snap_row(), s)
        self.assertEqual(cls, "on_model")
        self.assertNotIn("large_residual", tags)

    def test_xgi_window_needs_three_full_starts_then_flags_a_shift(self):
        row = snap_row(xg90=0.2, xa90=0.1)                # a 0.3 xGI/90 player
        rows = [dict(round=r, fixture_id=100 + r, mins=90, starts=1, xg=0.9, xa=0.4)
                for r in (1, 2, 3)]
        self.assertIsNone(retro.xgi_window(rows[:2], row, 3))
        got, exp, band = retro.xgi_window(rows, row, 3)
        self.assertAlmostEqual(got, 3.9)
        self.assertAlmostEqual(exp, 0.9)
        self.assertGreater(got - exp, band)
        cls, sub, tags, _ = self._classify(row, stats(expected_goals=0.9, expected_assists=0.4,
                                                       goals_scored=0),
                                           gw_rows=rows, gw=3)
        self.assertEqual((cls, sub), ("role_change", "xgi"))
        self.assertIn("xgi_shift", tags)

    def test_hauled_on_low_xg_is_tagged_variance(self):
        s = stats(goals_scored=2, expected_goals=0.6, bonus=3)
        cls, sub, tags, note = self._classify(snap_row(), s)
        self.assertEqual((cls, sub), ("variance", "finishing"))
        self.assertIn("hauled_low_xg", tags)
        self.assertIn("0.60 xG", note)

    def test_wording_never_says_form(self):
        for s in (stats(), stats(goals_scored=2, expected_goals=0.6, bonus=3),
                  stats(minutes=0, starts=0)):
            _, _, _, note = self._classify(snap_row(), s)
            self.assertNotIn("form", note.lower())


class ReviewAndHistory(unittest.TestCase):
    def test_review_writes_classes_streaks_and_projection_moves(self):
        snap = dict(gw=2, deadline="2026-08-28T17:30:00Z",
                    team_cs={"MCI": FIXTURE, "BRE": FIXTURE},
                    squad=[411, 106],
                    players=[snap_row(),
                             snap_row(id=106, name="Thiago", team="BRE", proj=5.0,
                                      p_start=0.97, baseline_start=0.97),
                             snap_row(id=7, name="Bench", proj=0.2, p_play=0.1)])
        s_h = stats()
        ex_h = explain_for(s_h)
        s_t = stats(minutes=0, starts=0, expected_goals=0.0, expected_assists=0.0,
                    defensive_contribution=0)
        ex_t = explain_for(s_t)
        actuals = dict(points={"411": [s_h["total_points"], 90, 1], "106": [0, 0, 0],
                               "7": [0, 0, 0]},
                       stats={"411": s_h, "106": s_t}, explain={"411": ex_h, "106": ex_t},
                       checked=True)
        elements = {411: dict(id=411, code=1, status="a", penalties_order=1),
                    106: dict(id=106, code=2, status="a", penalties_order=None),
                    7: dict(id=7, code=3, status="a")}
        new_proj = {411: dict(proj_by_gw=[0, 7.3, 7.5], baseline_start_rate=0.87),
                    106: dict(proj_by_gw=[0, 5.0, 4.9], baseline_start_rate=0.97)}
        previous = {106: [dict(gw=1, cls="minutes_loss", subtype="dnp")]}
        out = retro.review(2, snap, actuals, elements, new_proj, {}, {}, previous)
        by_id = {r["id"]: r for r in out["players"]}
        self.assertEqual(out["n_players"], 2)             # the bench row is out of scope
        self.assertEqual(by_id[411]["class"], "variance")
        self.assertEqual(by_id[106]["class"], "minutes_loss")
        self.assertEqual(by_id[106]["streak"], 2)
        self.assertIn("sell-grade", by_id[106]["note"])
        self.assertIn("NOT registered", by_id[106]["note"])
        self.assertEqual(by_id[106]["history"], [dict(gw=1, cls="minutes_loss")])
        self.assertEqual(by_id[411]["proj_next"], 7.5)
        self.assertEqual(by_id[411]["next_gw"], 3)
        self.assertIn("84% -> 87%", by_id[411]["start_move"])
        self.assertEqual(out["counts"], {"variance": 1, "minutes_loss": 1})
        self.assertEqual(out["n_unexplained"], 0)

    def test_roll_summary_accumulates_per_gameweek_counts(self):
        r1 = dict(gw=1, n_players=2, counts={"variance": 2}, generated="a")
        r2 = dict(gw=2, n_players=3, counts={"minutes_loss": 1, "on_model": 2}, generated="b")
        s1 = retro.roll_summary(r1)
        s2 = retro.roll_summary(r2, s1)
        self.assertEqual([g["gw"] for g in s2["gws"]], [1, 2])
        self.assertEqual(s2["latest_gw"], 2)
        again = retro.roll_summary(r2, s2)               # idempotent re-run
        self.assertEqual([g["gw"] for g in again["gws"]], [1, 2])

    def test_previous_retro_history_reads_up_to_five_prior_weeks(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(retro, "HISTORY", Path(tmp)):
            for g in (1, 2):
                (Path(tmp) / f"gw{g}_retro.json").write_text(json.dumps(
                    {"gw": g, "players": [{"id": 5, "class": "variance", "subtype": None}]}))
            prev = retro.load_previous_retro(3)
        self.assertEqual([h["gw"] for h in prev[5]], [1, 2])


if __name__ == "__main__":
    unittest.main()
