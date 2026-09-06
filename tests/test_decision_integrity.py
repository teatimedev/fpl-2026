import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from v2.decision_state import selling_value
from v2.minutes_survival import fit, probability
from v2.scorecard import grade
from v2.transfer_review import review, stress_player
from v2.decision_replay import freeze, grade_policies
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v2'))
import player_model as PM
import weekly
from test_planner_selling_prices import pool


class IntegrityTests(unittest.TestCase):
    def test_hold_action_discards_rejected_moves_and_keeps_its_actual_lineup(self):
        players, ids = pool()
        result = weekly.weekly_action('hold', [(8, 16)], 'Hold',
                                     [players[i] for i in ids], players, 3, 4)
        self.assertEqual(result['moves'], [])
        self.assertEqual(set(result['lineup']['xi'] + result['lineup']['bench']), set(ids))
        self.assertEqual(result['ft_next'], 4)
        self.assertEqual(result['hit_points'], 0)

    def test_transfer_action_lineup_contains_new_player_and_charges_real_hit(self):
        players, ids = pool()
        result = weekly.weekly_action('transfer', [(8, 16)], 'Move',
                                     [players[i] for i in ids], players, 0, 4)
        after = result['lineup']['xi'] + result['lineup']['bench']
        self.assertNotIn(8, after)
        self.assertIn(16, after)
        self.assertEqual(len(set(after)), 15)
        self.assertIn(result['lineup']['captain'], result['lineup']['xi'])
        self.assertEqual(result['hit_points'], 4)
        self.assertEqual(result['ft_next'], 1)

    def test_weekly_action_cap_and_preseason_free_rebuild(self):
        players, ids = pool()
        squad = [players[i] for i in ids]
        hold = weekly.weekly_action('hold', [], 'Hold', squad, players, 5, 4)
        use = weekly.weekly_action('transfer', [(8, 16)], 'Move', squad, players, 5, 4)
        rebuild = weekly.weekly_action('rebuild', [(8, 16)], 'Build', squad, players, 15, 1)
        self.assertEqual((hold['ft_next'], hold['ft_lost']), (5, 1))
        self.assertEqual((use['ft_next'], use['ft_lost']), (5, 0))
        self.assertEqual((rebuild['ft_next'], rebuild['hit_points']), (1, 0))

    def test_sale_rounding_and_loss(self):
        self.assertEqual(selling_value(55, 57), 56)
        self.assertEqual(selling_value(55, 56), 55)
        self.assertEqual(selling_value(80, 79), 79)

    def test_provisional_result_counts_but_scheduled_zero_row_does_not(self):
        self.assertTrue(PM.fixture_completed(dict(finished=False, finished_provisional=True, team_h_score=0, team_a_score=0)))
        self.assertFalse(PM.fixture_completed(dict(finished=False, finished_provisional=False, team_h_score=0, team_a_score=0)))
        self.assertFalse(PM.fixture_completed(dict(finished_provisional=True, team_h_score=None)))

    def test_p60_distinguishes_early_hooks_from_starts(self):
        fitted = fit({1: [(0, 1, 45)]*4, 2: [(0, 1, 90)]*4}, {1: 'MID', 2: 'MID'})
        self.assertLess(probability(fitted[1], .9, .05), probability(fitted[2], .9, .05))
        self.assertLessEqual(probability(fitted[1], .9, .05), .95)
        self.assertEqual(probability(fitted[1], 0, 0), 0)

    def test_negative_replacement_visible_and_unrealised_gain_not_spent(self):
        players, ids = pool()
        players[8]['proj_by_gw'] = [50.]*9
        players[16]['proj_by_gw'] = [1.]*9
        players[16]['price'] = 5.2
        squad = [players[i] for i in ids]
        rows = review(squad, players, 0, 1, 4, 4, {})['players']
        row = next(r for r in rows if r['player_id'] == 8)
        self.assertEqual(row['replacement'], 16)
        self.assertLess(row['net'], 0)
        actual = review(squad, players, 0, 1, 4, 4, {}, {8: 5.1})
        self.assertIsNone(next(r for r in actual['players'] if r['player_id'] == 8)['replacement'])

    def test_stress_does_not_mutate_original_and_changes_autosub_probability(self):
        p = dict(id=1, pos='MID', team='ARS', proj_by_gw=[5], start_by_gw=[.8], play_by_gw=[.9], mins_by_gw=[70])
        before = copy.deepcopy(p)
        changed = stress_player(p, {}, 1, 1, start_drop=.2)
        self.assertEqual(p, before)
        self.assertAlmostEqual(changed['play_by_gw'][0], .7)
        self.assertLess(changed['proj_by_gw'][0], 5)

    def test_legacy_yours_not_misgraded_and_tc_uses_actual_submission(self):
        players, ids = pool()
        for p in players.values(): p.update(proj=5, start_rate=.8)
        snap = dict(gw=3, generated='2026-09-01', players=list(players.values()), squad=ids,
                    yours=dict(captain=1, xi=ids[:11]), model=dict(captain=2, xi=ids[:11]))
        act = dict(points={str(i): [9 if i == 8 else 2, 90, 1] for i in players}, cs={})
        self.assertNotIn('yours', grade(snap, act)['captain'])
        submitted = dict(gw=3, entry_id=123, active_chip='3xc', entry_history=dict(points=47),
                         picks=[dict(element=i, position=n+1, multiplier=3 if i==8 else 1 if n<11 else 0,
                                     is_captain=i==8) for n,i in enumerate(ids)])
        result = grade(snap, act, submitted=submitted)
        self.assertEqual(result['captain']['yours']['id'], 8)
        self.assertEqual(result['submitted']['captain_multiplier'], 3)
        self.assertEqual(result['submitted']['credited_points'], 47)

    def test_closed_deadline_does_not_create_or_rewrite_forecast(self):
        with tempfile.TemporaryDirectory() as td, patch.object(weekly, 'HISTORY', Path(td)):
            deadline = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            with self.assertRaises(ValueError): weekly.snapshot(4, deadline, {}, [], {}, {})
            path = Path(td)/'gw4.json'; path.write_text('frozen')
            weekly.snapshot(4, deadline, {}, [], {}, {})
            self.assertEqual(path.read_text(), 'frozen')

    def test_policy_replay_uses_autosubs_vice_and_rejects_late_forecasts(self):
        players, ids = pool()
        policies = freeze(players, [players[i] for i in ids], 4, {})
        pol = policies[0]
        points = {str(i): [2, 90, 1] for i in ids}
        points[str(pol['captain'])] = [0, 0, 0]
        snap = dict(players=list(players.values()), policy_benchmarks=policies,
                    generated='2026-09-11T10:00:00Z', deadline='2026-09-12T12:30:00Z')
        result = grade_policies(snap, dict(points=points))
        self.assertEqual(result[0]['points'], 24)  # 11 playing + vice bonus
        snap['generated'] = '2026-09-13T00:00:00Z'
        self.assertEqual(grade_policies(snap, dict(points=points)), [])


if __name__ == '__main__':
    unittest.main()
