"""Undated flags decay over weeks; forced sales need no churn bar (GW9 2026/27)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v2'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from availability import expected_price_drop, recovery_probability  # noqa: E402
from planner import value_leak  # noqa: E402
from weekly import HOLD_THRESHOLD, MAX_FT, forced_out, move_bar, worth_rebuilding  # noqa: E402

CURVE = dict(availability={'i': [0.2, 0.3, 0.4, 0.4], 'd': [0.7, 0.8, 0.8, 0.9]},
             tail_rate={'i': 0.9, 'd': 0.7},
             price_drop={'i': [0.04, 0.06, 0.07, 0.08], 'd': [0.03, 0.05, 0.06, 0.07]})


class RecoveryCurveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'recovery.json'
        self.path.write_text(json.dumps(CURVE))

    def tearDown(self):
        self.tmp.cleanup()

    def rp(self, *args, **kw):
        return recovery_probability(*args, path=self.path, **kw)

    def test_unknown_return_injury_is_not_fit_next_week(self):
        news = 'Thigh injury - Unknown return date'
        self.assertEqual(self.rp('i', 9, 9, news=news), 1.0)   # deadline week: FPL chance applies
        self.assertAlmostEqual(self.rp('i', 10, 9, news=news), 0.2)
        self.assertAlmostEqual(self.rp('i', 12, 9, news=news), 0.4)

    def test_tail_closes_the_gap_without_reaching_certainty_at_once(self):
        later = [self.rp('i', 9 + k, 9) for k in range(4, 30)]
        self.assertEqual(later, sorted(later))
        self.assertAlmostEqual(later[1], 1 - 0.6 * 0.9)
        self.assertLess(later[-1], 1.0)

    def test_dated_and_fit_players_are_untouched(self):
        self.assertEqual(self.rp('i', 12, 9, news='Knee injury - Expected back 28 Nov'), 1.0)
        self.assertEqual(self.rp('a', 12, 9), 1.0)
        self.assertEqual(self.rp('u', 12, 9), 1.0)   # 'u' stays out via its status
        self.assertAlmostEqual(self.rp('d', 10, 9), 0.7)

    def test_missing_curve_keeps_the_old_behaviour(self):
        self.assertEqual(recovery_probability('i', 10, 9, path=Path(self.tmp.name) / 'none.json'), 1.0)

    def test_price_drop_grows_then_holds(self):
        drop = lambda k: expected_price_drop('i', k, path=self.path)
        self.assertEqual(drop(0), 0.0)
        self.assertAlmostEqual(drop(1), 0.04)
        self.assertAlmostEqual(drop(9), 0.08)
        self.assertEqual(expected_price_drop('a', 3, path=self.path), 0.0)


def player(pid, status='a', play=1.0, gw=9):
    return dict(id=pid, status=status, news='', play_by_gw=[0.0] * (gw - 1) + [play] * 6)


class ForcedMoveTests(unittest.TestCase):
    def test_selling_a_player_with_no_route_to_minutes_needs_no_bar(self):
        players = {1: player(1, 'i', 0.0), 2: player(2, 'u', 0.0), 3: player(3, 'd', 0.35),
                   4: player(4)}
        self.assertTrue(forced_out(players[1], 9))
        self.assertFalse(forced_out(players[4], 9))
        self.assertEqual(move_bar([1, 2, 3], players, 9, 4), 0.0)
        self.assertEqual(move_bar([1, 2, 3, 4], players, 9, 4), HOLD_THRESHOLD)
        # GW9: four moves, three forced, +7.65 over holding — now clears.
        self.assertTrue(worth_rebuilding(7.65, 4, move_bar([1, 2, 3, 4], players, 9, 4)))

    def test_a_full_bank_makes_one_move_free_of_option_value(self):
        players = {4: player(4), 5: player(5)}
        self.assertEqual(move_bar([4], players, 9, MAX_FT), 0.0)
        self.assertEqual(move_bar([4, 5], players, 9, MAX_FT), HOLD_THRESHOLD)

    def test_zero_bar_still_needs_a_positive_gain(self):
        self.assertFalse(worth_rebuilding(0.0, 1, 0.0))
        self.assertFalse(worth_rebuilding(-0.5, 1, 0.0))

    def test_flagged_owner_loses_value_the_later_he_is_sold(self):
        P = {1: dict(player(1, 'i', 0.0), news='Unknown return date'), 2: player(2)}
        leak = value_leak(P, [1, 2], {}, [9, 10, 11], 9)
        self.assertEqual(set(leak), {1})
        self.assertEqual(leak[1][9], 0.0)
        self.assertGreater(leak[1][11], leak[1][10])
        # in profit, FPL keeps half a rise, so roughly half the drop passes through
        half = value_leak(P, [1], {1: 2}, [9, 10], 9)
        self.assertAlmostEqual(half[1][10], leak[1][10] / 2)


if __name__ == '__main__':
    unittest.main()


class MustSellTests(unittest.TestCase):
    def test_must_sell_clears_the_player_and_lets_the_solver_pick_cover(self):
        from test_planner_selling_prices import pool
        from planner import plan
        players, owned = pool()
        result = plan(players, owned, 0, 2, 4, 5, must_sell=[8])
        self.assertNotIn(8, result['weeks'][0]['squad'])
        self.assertIn(16, result['weeks'][0]['squad'])


class ClubMoveTests(unittest.TestCase):
    def test_a_club_move_that_leaves_four_from_one_club_can_still_be_held(self):
        from test_planner_selling_prices import pool
        from planner import plan
        players, owned = pool()
        for pid in (1, 3, 4, 5):                 # four owned players now at T1
            players[pid]['team'] = 'T1'
        held = plan(players, owned, 0, 1, 4, 5, freeze_this_week=True)
        self.assertIsNotNone(held)
        players[16]['team'] = 'T1'               # but a fifth cannot be bought
        moved = plan(players, owned, 0, 1, 4, 5)
        self.assertNotIn(16, moved['weeks'][0]['squad'])
