import unittest
from copy import deepcopy
from v2.case_studies import summarise
from v2.policy_lab import compare_buffers
from v2.decision_replay import freeze
from v2.transfer_review import paired_stress
from test_planner_selling_prices import pool


class PolicyEvidenceTests(unittest.TestCase):
    def test_scheduled_zero_row_is_not_an_extra_bad_match(self):
        row = dict(fixture_id='1', round='3', minutes='90', starts='1', points='2', goals='0',
                   assists='0', xg='.5', xa='.01', pens_missed='1')
        report = summarise(106, 'Thiago', [row, dict(row, fixture_id='2', minutes='0')], {1})
        self.assertEqual(len(report['matches']), 1)
        self.assertEqual(report['totals']['xg'], .5)
        self.assertEqual(report['totals']['penalties_missed'], 1)

    def test_policy_buffer_is_separate_from_banking_and_never_forces_negative_gain(self):
        grid = compare_buffers(.39, 3)
        self.assertEqual([r['decision'] for r in grid[:3]], ['act', 'act', 'hold'])
        self.assertEqual(grid[-1]['required'], 6)
        self.assertTrue(all(r['decision']=='hold' for r in compare_buffers(-.01, 1)))
        self.assertTrue(all(r['decision']=='hold' for r in compare_buffers(0, 0)))

    def test_frozen_policies_keep_real_first_week_hits(self):
        players, ids = pool()
        plan = dict(diff=.4, diff_unrounded=.39, weeks=[dict(out=[8], in_=[16], hits=1)])
        rows = freeze(players, [players[i] for i in ids], 4, {}, plan=plan)
        immediate = next(r for r in rows if r['label']=='planner_buffer_0')
        hold = next(r for r in rows if r['label']=='planner_buffer_2')
        self.assertIn(16, immediate['squad'])
        self.assertEqual(immediate['hit'], 4)
        self.assertEqual(set(hold['squad']), set(ids))
        self.assertEqual(hold['hit'], 0)

    def test_stressing_incoming_can_erase_a_transfer_advantage(self):
        players, ids = pool()
        for p in players.values():
            p.update(mins_by_gw=[90.]*9, xg90=.5, xa90=.2, calibration_k=1.)
        players[8]['proj_by_gw'] = [15.]*9
        players[16]['proj_by_gw'] = [15.1]*9
        before = deepcopy(players)
        view = {p['team']:{'4':[{'xg':1.45}]} for p in players.values()}
        grid = paired_stress([players[i] for i in ids], players[8], players[16], view, 4, 4)
        self.assertLess(grid[2]['net'], grid[0]['net'])
        self.assertEqual(players, before)
