"""Phase 4: week decay and terminal value in the path MILP."""
import sys
import unittest
from pathlib import Path

V2 = Path(__file__).resolve().parents[1] / "v2"
sys.path.insert(0, str(V2))
from planner import (LEGACY, Valuation, ft_terminal_value, plan,  # noqa: E402
                     production_valuation, tail_player)

SHAPE = [('GKP', 2), ('DEF', 5), ('MID', 5), ('FWD', 3)]


def squad(points=4.0, weeks=12, extra=()):
    """15 owned players (one club each) plus extra (pid, pos, window, price)."""
    players, owned, pid = {}, [], 0
    for pos, n in SHAPE:
        for _ in range(n):
            pid += 1
            players[pid] = dict(id=pid, name=f'P{pid}', pos=pos, price=5.0, team=f'T{pid}',
                                status='a', proj_by_gw=[points] * weeks,
                                play_by_gw=[0.9] * weeks)
            owned.append(pid)
    for new_id, pos, proj, price in extra:
        players[new_id] = dict(id=new_id, name=f'N{new_id}', pos=pos, price=price,
                               team=f'N{new_id}', status='a', proj_by_gw=list(proj),
                               play_by_gw=[0.9] * weeks)
    return players, owned


def full_tail(players, overrides=None, weeks=38):
    tail = {}
    for pid, p in players.items():
        value = (overrides or {}).get(pid, p['proj_by_gw'][-1])
        tail[pid] = dict(id=pid, pos=p['pos'], team=p['team'], status='a',
                         proj_by_gw=[value] * weeks, play_by_gw=[0.9] * weeks)
    return tail


class ValuationTests(unittest.TestCase):
    def test_week_weights_decay_geometrically(self):
        v = production_valuation(decay=0.9)
        self.assertEqual(v.weight(6, 6), 1.0)
        self.assertAlmostEqual(v.weight(6, 8), 0.81)
        self.assertEqual(LEGACY.weight(6, 11), 1.0)

    def test_banked_transfer_value_is_concave_beyond_the_first(self):
        values = (2.0, 1.6, 1.3, 1.1)
        self.assertEqual(ft_terminal_value(1, values), 0)
        self.assertAlmostEqual(ft_terminal_value(3, values), 3.6)
        self.assertAlmostEqual(ft_terminal_value(5, values), 6.0)
        self.assertAlmostEqual(ft_terminal_value(9, values), 6.0)

    def test_tail_falls_back_to_the_late_window_rate(self):
        p = dict(id=1, pos='MID', team='A', proj_by_gw=[0, 0, 2.0, 4.0, 6.0] + [0] * 5,
                 play_by_gw=[0.8] * 10)
        t = tail_player(p, Valuation(tail_weeks=3), 3, 5)
        self.assertEqual(t['proj_by_gw'][6], 5.0)          # mean of GW4-5
        seasonal = tail_player(p, Valuation(tail_weeks=3, tail={1: dict(
            proj_by_gw=[1.0] * 38, play_by_gw=[0.5] * 38)}), 3, 5)
        self.assertEqual(seasonal['proj_by_gw'][6], 1.0)


class TerminalValuePlannerTests(unittest.TestCase):
    def test_no_last_week_churn_for_less_than_a_banked_transfer_is_worth(self):
        # A +0.5 upgrade (+1.0 as captain) that only exists in the final week.
        players, owned = squad(extra=[(99, 'MID', [4.0] * 3 + [4.5] + [4.0] * 8, 5.0)])
        legacy = plan(players, owned, 0.0, 1, 2, 4)
        valued = plan(players, owned, 0.0, 1, 2, 4,
                      valuation=production_valuation(tail=full_tail(players)))
        self.assertIn(99, legacy['weeks'][-1]['squad'])     # spends a transfer on it
        self.assertEqual([w['in'] for w in valued['weeks']], [[], [], []])
        self.assertEqual(valued['terminal']['ft_end'], 4)
        self.assertAlmostEqual(valued['terminal']['ft_value'], 4.9)

    def test_long_term_dead_weight_is_sold_even_when_it_scores_in_the_window(self):
        # Player 3 (a DEF) plays in the window but leaves after it (a loan
        # ending, a sale): zero from GW4 on. The replacement scores slightly
        # less now but keeps scoring. Only the terminal tail can see that.
        players, owned = squad(extra=[(99, 'DEF', [3.8] * 12, 5.0)])
        tail = full_tail(players, overrides={3: 0.0})
        legacy = plan(players, owned, 0.0, 1, 2, 3)
        valued = plan(players, owned, 0.0, 1, 2, 3,
                      valuation=production_valuation(tail=tail))
        self.assertTrue(all(3 in w['squad'] for w in legacy['weeks']))
        self.assertNotIn(3, valued['weeks'][-1]['squad'])
        self.assertIn(99, valued['weeks'][-1]['squad'])
        self.assertGreater(valued['terminal']['tail_value'], 0)

    def test_objective_matches_its_parts(self):
        players, owned = squad()
        v = production_valuation(tail=full_tail(players))
        res = plan(players, owned, 1.0, 2, 2, 4, valuation=v)
        decayed = sum(v.weight(2, w['gw']) * (w['pts'] - 4 * w['hits']) for w in res['weeks'])
        t = res['terminal']
        self.assertAlmostEqual(res['objective'],
                               decayed + t['ft_value'] + t['bank_value'] + t['tail_value'],
                               delta=0.2)          # week points are rounded to 0.1
        self.assertAlmostEqual(t['bank_end'], 1.0)
        self.assertAlmostEqual(t['bank_value'], 0.08)


if __name__ == '__main__':
    unittest.main()
