"""Player-model unit tests for the in-season learning plan (P0, P2, P4, P5).

player_model.py uses bare in-package imports (``from gwclock import ...``), so
it is imported the way tests/test_infer_ft.py imports weekly: with v2/ on the
path. Module globals that main() normally fills (GAMES_PLAYED, TEAM_FIXTURES,
SNAPSHOT_STATUS, PRICE_MEDIAN, the calibration file) are patched per test so
nothing here depends on the cached bootstrap or the SQLite database.
"""
import json
import math
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "v2"
sys.path.insert(0, str(V2))
sys.path.insert(0, str(ROOT))

import player_model as PM  # noqa: E402
from v2.fetch import schema  # noqa: E402


def season_row(season, mins, starts, pts=0, xg=0.0, xa=0.0):
    p90 = mins / 90.0 if mins else None
    per90 = (lambda v: v / p90) if p90 else (lambda v: 0.0)
    return dict(season=season, mins=mins, starts=starts, pts=pts,
                pts90=per90(pts), xg90=per90(xg), xa90=per90(xa), dc90=0.0,
                bonus90=0.0, saves90=0.0, yellow90=0.0, g=0, a=0, cs=0,
                defcon_raw=0, xg=xg, xa=xa)


def make_player(pid=999_001, team="MCI", pos="FWD", price=8.0, hist=None,
                now=None, joined="", gw=None):
    return dict(id=pid, code=pid, name=f"p{pid}", full_name=f"p{pid}", team=team,
                pos=pos, price=price, sel_pct=1.0, status="a", news="",
                chance=None, joined=joined, dob="1999-01-01", pens=None,
                corners=None, fk=None, hist=hist or [], now=now, gw=gw or [])


def fixtures(n, team="MCI"):
    """n finished club fixtures, one per event, in kickoff order."""
    return {team: [dict(fixture_id=100 + i, event=i,
                        kickoff=f"2026-08-{14 + i:02d}T15:00:00Z")
                   for i in range(1, n + 1)]}


def gw_rows(seq):
    """seq = [(fixture_id, minutes, starts)] -> player gw rows."""
    return [dict(fixture_id=f, round=f - 100, kickoff="", mins=m, starts=s)
            for f, m, s in seq]


# ---------------------------------------------------------------------- P0
class LoadKeepsCurrentSeasonZeroRow(unittest.TestCase):
    """P0 / W1: the current season's zero-minute row is the record of a player
    who did not play while his team did. It must reach the minutes model."""

    def _db_with(self, rows):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "fpl.db"
        cx = sqlite3.connect(db)
        schema(cx)
        cx.execute(
            "INSERT INTO player VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, 5001, "Thiago", "Thiago Alves", "BRE", 1, "FWD", 8.0, 17.3,
             "a", "", None, "2025-07-01", "2001-01-01", None, None, None))
        for season, mins, starts in rows:
            cx.execute(
                "INSERT INTO season_stat (code, season, minutes, starts, points) "
                "VALUES (?,?,?,?,?)", (5001, season, mins, starts, 0))
        cx.commit()
        cx.close()
        return db

    def test_current_zero_row_kept_past_zero_rows_dropped(self):
        db = self._db_with([("2024/25", 0, 0), ("2025/26", 3282, 36),
                            (PM.CURRENT, 0, 0)])
        with patch.object(PM, "DB", db):
            players = PM.load()
        p = players[1]
        self.assertEqual([h["season"] for h in p["hist"]], ["2025/26", PM.CURRENT])
        self.assertIsNotNone(p["now"])
        self.assertEqual(p["now"]["mins"], 0)
        self.assertEqual(p["now"]["starts"], 0)
        # a zero row must not poison the per-90 rates with a division by zero
        self.assertEqual(p["now"]["xg90"], 0.0)
        self.assertEqual(p["now"]["pts90"], 0.0)
        self.assertEqual(p["gw"], [])

    def test_player_without_a_current_row_has_no_now(self):
        db = self._db_with([("2025/26", 3282, 36)])
        with patch.object(PM, "DB", db):
            players = PM.load()
        self.assertIsNone(players[1]["now"])

    def test_zero_rows_never_reach_shrink_or_the_positional_prior(self):
        p = make_player(hist=[season_row("2025/26", 2700, 30, xg=15.0),
                              season_row(PM.CURRENT, 0, 0)])
        p["now"] = p["hist"][-1]
        priors = PM.positional_priors({p["id"]: p})
        # only the 2025/26 row (450+ minutes) feeds the prior
        self.assertAlmostEqual(priors["FWD"]["xg90"], 15.0 / 30.0)
        est, w = PM.shrink(p, "xg90", priors)
        # n_eff = 2700/FULL_SEASON_MINS, k = (1-0.91)/0.91 -> w = n/(n+k) = 0.891
        self.assertGreater(w, 0.88)
        self.assertAlmostEqual(est, 15.0 / 30.0, places=6)


class MinutesModelAggregateRule(unittest.TestCase):
    """The production (aggregate) in-season update: starts / team games,
    trusted n / (n + CURRENT_TRUST_K)."""

    def setUp(self):
        self.hist = [season_row("2025/26", 3000, 34)]

    def _rate(self, now, games, rule="aggregate"):
        p = make_player(hist=list(self.hist), now=now)
        players = {p["id"]: p}
        with patch.dict(PM.GAMES_PLAYED, {"MCI": games}, clear=True), \
                patch.dict(PM.TEAM_FIXTURES, {}, clear=True), \
                patch.object(PM, "GW_ROWS_LOADED", False), \
                patch.object(PM, "MINUTES_RULE", rule):
            return PM.minutes_model(p, players)

    def test_no_current_row_leaves_the_rate_untouched(self):
        base, _ = self._rate(None, 1)
        self.assertGreater(base, 0.8)
        again, _ = self._rate(None, 3)
        self.assertAlmostEqual(base, again)

    def test_zero_minute_row_after_one_team_game_is_a_benching(self):
        base, base_mps = self._rate(None, 1)
        now = dict(season_row(PM.CURRENT, 0, 0))
        rate, mps = self._rate(now, 1)
        trust = 1.0 / (1.0 + PM.CURRENT_TRUST_K)          # 0.2 after one game
        self.assertAlmostEqual(rate, (1 - trust) * base, places=6)
        self.assertAlmostEqual(mps, base_mps)              # no start: mps unchanged

    def test_starting_every_game_moves_the_rate_up(self):
        base, _ = self._rate(None, 2)
        now = dict(season_row(PM.CURRENT, 180, 2))
        rate, mps = self._rate(now, 2)
        trust = 2.0 / (2.0 + PM.CURRENT_TRUST_K)
        self.assertAlmostEqual(rate, min(0.97, trust * 1.0 + (1 - trust) * base), places=6)
        self.assertGreaterEqual(mps, 85.0)

    def test_recency_rule_without_any_per_fixture_rows_falls_back_to_aggregate(self):
        now = dict(season_row(PM.CURRENT, 0, 0))
        agg, _ = self._rate(now, 1, rule="aggregate")
        rec, _ = self._rate(now, 1, rule="recency")
        self.assertAlmostEqual(agg, rec)

class ManagerMinutesBlendTests(unittest.TestCase):
    def setUp(self):
        self.player = make_player(pid=101, team="MCI", pos="FWD")
        self.table = {
            "cells": {
                ("MCI", PM.CURRENT, "FWD"): {
                    "n": 2,
                    "raw_mps": 75.0,
                    "mps": 78.0,
                    "contributions": {
                        101: {"n": 1, "minutes_sum": 90.0},
                        202: {"n": 1, "minutes_sum": 60.0},
                    },
                },
            },
            "league": {
                (PM.CURRENT, "FWD"): {
                    "n": 20, "mps": 80.0, "hook_rate": 0.2, "full90": 0.5,
                },
            },
            "provenance": {},
        }

    def test_blends_mps_but_never_start_probability(self):
        rate, mps = PM.manager_minutes_blend(
            self.player, (0.9, 90.0), table=self.table, weight=0.25
        )

        # Excluding the target leaves a 60-minute teammate and also removes
        # his 90 from the league-position shrinkage prior.
        league_mps = (80.0 * 20 - 90.0) / 19
        manager_mps = (1 / 7) * 60.0 + (6 / 7) * league_mps
        self.assertEqual(rate, 0.9)
        self.assertAlmostEqual(mps, 0.75 * 90.0 + 0.25 * manager_mps)

    def test_missing_production_table_is_inert(self):
        self.assertEqual(
            PM.manager_minutes_blend(self.player, (0.9, 90.0), table=None),
            (0.9, 90.0),
        )


# ---------------------------------------------------------------------- P2
class MatchEvidenceTests(unittest.TestCase):
    """Which fixtures count as evidence about the manager's selection."""

    def _evidence(self, rows, snaps, n=3, joined=""):
        p = make_player(gw=rows, joined=joined)
        return PM.match_evidence(p, fixtures=fixtures(n), snapshots=snaps)

    def test_healthy_non_starts_are_evidence_most_recent_first(self):
        rows = gw_rows([(101, 0, 0), (102, 0, 0), (103, 0, 0)])
        snaps = {g: {999_001: ("a", 0.9)} for g in (1, 2, 3)}
        ev = self._evidence(rows, snaps)
        self.assertEqual(ev, [(0, 0, 0), (1, 0, 0), (2, 0, 0)])

    def test_flagged_or_overridden_absences_are_not_evidence(self):
        rows = gw_rows([(101, 0, 0), (102, 0, 0), (103, 90, 1)])
        snaps = {1: {999_001: ("i", 0.05)}, 2: {999_001: ("a", 0.0)},
                 3: {999_001: ("a", 0.9)}}
        ev = self._evidence(rows, snaps)
        self.assertEqual(ev, [(0, 1, 90)])

    def test_played_minutes_count_even_when_flagged_doubtful(self):
        rows = gw_rows([(101, 25, 0)])
        snaps = {1: {999_001: ("d", 0.4)}}
        self.assertEqual(self._evidence(rows, snaps, n=1), [(0, 0, 25)])

    def test_fixtures_before_joining_and_without_rows_are_skipped(self):
        rows = gw_rows([(102, 0, 0), (103, 90, 1)])       # no row for fixture 101
        snaps = {g: {999_001: ("a", 0.8)} for g in (1, 2, 3)}
        ev = self._evidence(rows, snaps, joined="2026-08-17")   # fixture 102 is 16 Aug
        self.assertEqual(ev, [(0, 1, 90)])

    def test_missing_snapshot_assumes_available(self):
        rows = gw_rows([(101, 0, 0)])
        self.assertEqual(self._evidence(rows, {}, n=1), [(0, 0, 0)])

    def test_double_gameweek_gives_two_observations(self):
        fx = {"MCI": [dict(fixture_id=101, event=1, kickoff="2026-08-15T15:00:00Z"),
                      dict(fixture_id=102, event=1, kickoff="2026-08-18T19:00:00Z")]}
        p = make_player(gw=gw_rows([(101, 90, 1), (102, 0, 0)]))
        ev = PM.match_evidence(p, fixtures=fx, snapshots={1: {999_001: ("a", 0.9)}})
        self.assertEqual(ev, [(0, 0, 0), (1, 1, 90)])

    def test_starts_missing_falls_back_to_sixty_minutes(self):
        rows = [dict(fixture_id=101, round=1, kickoff="", mins=75, starts=None),
                dict(fixture_id=102, round=2, kickoff="", mins=20, starts=None)]
        ev = self._evidence(rows, {}, n=2)
        self.assertEqual(ev, [(0, 0, 20), (1, 1, 75)])


class RecencyRuleTests(unittest.TestCase):
    """The hand checks from the plan (P2), at the placeholder constants."""

    def _update(self, evidence, prior=0.9, mps=85.0, k=None, hl=None):
        p = make_player()
        return PM.recency_update(p, prior, mps, k=k, half_life=hl, evidence=evidence)

    def test_no_evidence_returns_the_prior(self):
        self.assertEqual(self._update([]), (0.9, 85.0))

    def test_weights_halve_every_half_life_games(self):
        ev = [(0, 1, 90), (3, 0, 0)]                     # one start now, one benching 3 games ago
        rate, _ = self._update(ev, prior=0.5, k=0.0, hl=3.0)
        # trust = 1 with k = 0: rate_now = 1 / (1 + 0.5)
        self.assertAlmostEqual(rate, 1.0 / 1.5, places=6)
        rate_inf, _ = self._update(ev, prior=0.5, k=0.0, hl=math.inf)
        self.assertAlmostEqual(rate_inf, 0.5, places=6)

    def test_one_rest_in_six_stays_above_0_8(self):
        ev = [(g, 0 if g == 2 else 1, 90) for g in range(6)]
        rate, _ = self._update(ev)
        self.assertGreater(rate, 0.8)

    def test_three_straight_benchings_at_measured_constants(self):
        ev = [(0, 0, 0), (1, 0, 0), (2, 0, 0)]
        rate, _ = self._update(ev)                       # K=1, HL=3 (measured 27 Aug)
        # documented in player_model.py: a 0.9 regular is ~0.26 after three
        # straight benchings — the < 0.4 the plan asked for
        self.assertLess(rate, 0.4)
        self.assertGreater(rate, 0.15)
        loose, _ = self._update(ev, k=4.0)               # the old placeholder: ~0.56
        self.assertGreater(loose, 0.5)
        one, _ = self._update([(0, 0, 0)])               # one benching: ~0.45
        self.assertLess(one, 0.5)

    def test_minutes_per_start_keep_their_own_trust(self):
        # K=1 would let one 67' start pull a 90-minute regular to ~78; the
        # mps half of the rule keeps RECENCY_MPS_K (4) so it moves to ~85
        _, mps = self._update([(0, 1, 67)], mps=90.0)
        self.assertGreater(mps, 84.0)
        self.assertLess(mps, 86.0)

    def test_returning_absentee_is_judged_on_games_since_return(self):
        # out (flagged) for six fixtures, started the two since coming back
        rows = gw_rows([(100 + i, 0, 0) for i in range(1, 7)] + [(107, 90, 1), (108, 90, 1)])
        snaps = {g: {999_001: ("i", 0.05)} for g in range(1, 7)}
        snaps.update({7: {999_001: ("a", 0.8)}, 8: {999_001: ("a", 0.8)}})
        p = make_player(hist=[season_row("2025/26", 3000, 34)], gw=rows,
                        now=season_row(PM.CURRENT, 180, 2))
        players = {p["id"]: p}
        with patch.dict(PM.TEAM_FIXTURES, fixtures(8), clear=True), \
                patch.dict(PM.SNAPSHOT_STATUS, snaps, clear=True), \
                patch.dict(PM.GAMES_PLAYED, {"MCI": 8}, clear=True), \
                patch.object(PM, "GW_ROWS_LOADED", True):
            prior, _ = PM.minutes_prior(p, players)
            recency, _ = PM.minutes_model(p, players, rule="recency")
            aggregate, _ = PM.minutes_model(p, players, rule="aggregate")
        self.assertGreater(recency, prior)          # two starts since return: up
        self.assertLess(aggregate, 0.5)             # 2/8 games: the W2 failure
        self.assertGreater(recency, aggregate)

    def test_minutes_per_start_ignores_cameo_minutes(self):
        ev = [(0, 0, 10), (1, 1, 90), (2, 1, 88)]
        _, mps = self._update(ev, prior=0.8, mps=70.0)
        self.assertGreater(mps, 70.0)
        self.assertLessEqual(mps, 90.0)
        # a pure cameo record leaves minutes per start alone
        _, mps_cameo = self._update([(0, 0, 10), (1, 0, 15)], prior=0.8, mps=70.0)
        self.assertEqual(mps_cameo, 70.0)

    def test_rate_is_capped_like_the_aggregate_rule(self):
        ev = [(g, 1, 90) for g in range(20)]
        rate, _ = self._update(ev, prior=0.99)
        self.assertLessEqual(rate, 0.97)


class SnapshotStatusLoader(unittest.TestCase):
    def test_reads_status_and_deadline_start_probability_per_gameweek(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "gw1.json").write_text(json.dumps({
                "gw": 1, "players": [{"id": 5, "status": "a", "p_start": 0.7},
                                     {"id": 6, "status": "i", "start_rate": 0.9}]}))
            (d / "gw1_actual.json").write_text("{}")     # ignored
            (d / "gw1_retro.json").write_text("{}")      # ignored
            out = PM.load_snapshot_status(d)
        self.assertEqual(out, {1: {5: ("a", 0.7), 6: ("i", 0.9)}})


# ---------------------------------------------------------------------- P4
class CalibrationTests(unittest.TestCase):
    def test_feedback_blend_guards(self):
        ks = {"FWD": 1.0, "MID": 1.1}
        same, moved = PM.feedback_blend(ks, {"FWD": 1.3}, n_gws=7)
        self.assertEqual((same, moved), (ks, []))                 # too few gameweeks
        same, moved = PM.feedback_blend(ks, {"FWD": 1.08}, n_gws=10)
        self.assertEqual((same, moved), (ks, []))                 # inside the drift band
        new, moved = PM.feedback_blend(ks, {"FWD": 1.2}, n_gws=8, k_c=8.0)
        self.assertEqual(moved, ["FWD"])
        self.assertAlmostEqual(new["FWD"], 0.5 * 1.0 + 0.5 * 1.2, places=4)
        self.assertEqual(new["MID"], 1.1)

    def test_apply_calibration_scales_rows_and_records_k(self):
        rows = [dict(id=1, pos="FWD", proj_by_gw=[1.0, 2.0], price=8.0),
                dict(id=2, pos="MID", proj_by_gw=[1.0, 1.0], price=5.0)]
        with patch.object(PM, "WINDOW", 2), patch.dict(PM.SEASON, {1: [1.0, 2.0, 3.0]}, clear=True):
            PM.apply_calibration(rows, {"FWD": 1.1})
            self.assertEqual(PM.SEASON[1], [1.1, 2.2, 3.3])
        self.assertEqual(rows[0]["proj_by_gw"], [1.1, 2.2])
        self.assertEqual(rows[0]["proj_6gw"], 3.3)
        self.assertEqual(rows[0]["calibration_k"], 1.1)
        self.assertNotIn("calibration_k", rows[1])
        self.assertEqual(rows[1]["proj_by_gw"], [1.0, 1.0])

    def test_fit_excludes_cohort_members_depressed_by_availability(self):
        players = {}
        rows = []
        with patch.multiple(PM, START_GW=1, HORIZON=2):
            for i in range(8):
                p = make_player(pid=i, hist=[season_row("2025/26", 2500, 28, pts=152),
                                             season_row("2024/25", 2500, 28, pts=152)])
                players[i] = p
                rows.append(dict(id=i, pos="FWD", proj_gw=2.0, baseline_start_rate=0.9,
                                 start_by_gw=[0.9, 0.9]))
            # a long-term absentee projects ~0 over the window
            rows[0].update(proj_gw=0.1, start_by_gw=[0.05, 0.05])
            fit = PM.fit_calibration(rows, players)
        self.assertEqual(fit["FWD"]["n"], 7)
        self.assertEqual(fit["FWD"]["n_excluded"], 1)
        self.assertAlmostEqual(fit["FWD"]["ratio"], 2.0 / 4.0, places=4)
        self.assertAlmostEqual(fit["FWD"]["k"], 1.45, places=4)   # clipped

    def _project(self, xg_scale, calibration_dir):
        p = make_player(pid=999_101, hist=[season_row("2025/26", 3000, 34, pts=200,
                                                       xg=20.0, xa=5.0)])
        players = {p["id"]: p}
        priors = {"FWD": dict(xg90=0.4, xa90=0.15, dc90=0.0, bonus90=0.2,
                              saves90=0.0, yellow90=0.1)}
        fx = {str(gw): [dict(opp="X", home=True, xg=1.5 * xg_scale, xgc=1.2, cs=0.3)]
              for gw in range(1, 7)}
        view = {"view": {"MCI": fx}}
        cal = Path(calibration_dir) / "calibration.json"
        cal.write_text(json.dumps({"fitted_at": "test", "k": {"FWD": {"k": 1.0}}}))
        with patch.multiple(PM, START_GW=1, HORIZON=6, LAST_GW=6, WINDOW=6,
                            CALIBRATION=cal, GW_DEADLINES={}, AVAILABILITY_OVERRIDES=[],
                            OVERLAY={}, PRESEASON_FORM={}, MINUTES_RULE="aggregate",
                            GW_ROWS_LOADED=False), \
                patch.dict(PM.SEASON, {}, clear=True), \
                patch.dict(PM.GAMES_PLAYED, {}, clear=True), \
                patch.dict(PM.TEAM_FIXTURES, {}, clear=True), \
                patch.dict(PM.SNAPSHOT_STATUS, {}, clear=True):
            rows = PM.project(players, view, priors)
        return rows[0]

    def test_frozen_calibration_lets_fixture_xg_move_the_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._project(1.0, tmp)
            up = self._project(1.1, tmp)
        self.assertEqual(base["calibration_k"], 1.0)
        ratio = up["proj_6gw"] / base["proj_6gw"]
        # +10% on every fixture's xG lifts a forward's total by his attack
        # share of that — well clear of the ~0% a re-fitting k produced
        self.assertGreater(ratio, 1.03)
        self.assertLess(ratio, 1.10)
        # and the new P2/P3 fields are on the row
        for key in ("baseline_start_rate", "start_rate_recency", "start_rate_aggregate",
                    "bonus90", "saves90", "yellow90", "dc_evidence", "minutes_rule"):
            self.assertIn(key, base)
        self.assertEqual(len(base["start_recency_by_gw"]), 6)


# ---------------------------------------------------------------------- P5
class ContextMultiplierTests(unittest.TestCase):
    def test_default_multiplier_is_one_until_measured(self):
        stayer = make_player(joined="2019-07-01", team="ARS")
        mover = make_player(joined="2026-07-01", team="ARS")
        self.assertEqual(PM.context_multiplier(stayer), 1.0)
        self.assertTrue(PM.context_changed(mover))
        self.assertEqual(PM.context_multiplier(mover), PM.CONTEXT_CURRENT_MULT)
        self.assertEqual(PM.CONTEXT_CURRENT_MULT, 1.0)

    def test_current_mult_raises_the_current_season_weight(self):
        p = make_player(hist=[season_row("2025/26", 2700, 30, xg=6.0),      # 0.20 xG/90
                              season_row(PM.CURRENT, 900, 10, xg=6.0)])     # 0.60 xG/90
        priors = {"FWD": dict(xg90=0.3)}
        plain, _ = PM.shrink(p, "xg90", priors, current_mult=1.0)
        boosted, _ = PM.shrink(p, "xg90", priors, current_mult=3.0)
        default, _ = PM.shrink(p, "xg90", priors)
        self.assertAlmostEqual(default, plain)
        self.assertGreater(boosted, plain)
        # with the current season at triple weight it is half the evidence
        own_plain = (0.2 * 2700 + 0.6 * 900) / (2700 + 900)
        own_boost = (0.2 * 2700 + 0.6 * 2700) / (2700 + 2700)
        self.assertLess(abs(plain - own_plain), 0.02)
        self.assertLess(abs(boosted - own_boost), 0.02)


if __name__ == "__main__":
    unittest.main()
