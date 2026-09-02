"""Deterministic, synthetic contract tests for :mod:`v2.decision_sim`."""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from v2.decision_sim import (  # noqa: E402
    POS_IX,
    _player_params,
    _simulate_player,
    _squad_gw_points,
    _team_draws,
    compare,
)

START_GW, HORIZON = 1, 2
N_SIMS = 1200
SEED = 20260902

# Neutral shared clubs, two favourable replacement clubs, and two weak clubs.
FIXTURE_XG = {
    'N1': {1: (1.4, 1.2), 2: (1.3, 1.1)},
    'N2': {1: (1.5, 1.3), 2: (1.4, 1.2)},
    'N3': {1: (1.3, 1.1), 2: (1.5, 1.2)},
    'N4': {1: (1.4, 1.2), 2: (1.4, 1.2)},
    'CHE': {1: (2.5, 0.8), 2: (2.4, 0.9)},
    'LIV': {1: (2.3, 0.9), 2: (2.4, 0.8)},
    'SUN': {1: (0.8, 2.0), 2: (0.9, 2.1)},
    'IPS': {1: (0.9, 1.9), 2: (0.8, 2.0)},
}


def _player(pid, name, pos, team, proj, xg90, xa90, dc90=4.0, start=0.95):
    play = min(1.0, start + 0.03)
    availability = [
        {'start_minutes': 85.0, 'cameo_minutes': 22.0},
        {'start_minutes': 85.0, 'cameo_minutes': 22.0},
    ]
    return {
        'id': pid, 'name': name, 'pos': pos, 'team': team,
        'proj_by_gw': [proj, proj],
        'start_by_gw': [start, start], 'play_by_gw': [play, play],
        'mins_by_gw': [start * 85 + (play - start) * 22] * 2,
        'availability_by_gw': availability,
        'xg90': xg90, 'xa90': xa90, 'bonus90': 0.4, 'yellow90': 0.1,
        'dc90': 0.0 if pos == 'GKP' else dc90,
        'saves90': 3.0 if pos == 'GKP' else 0.0,
    }


PLAYERS = {
    # Shared legal core: 2 GKP, 3 DEF, 3 MID, 3 FWD.  No club exceeds three.
    1: _player(1, 'Keeper1', 'GKP', 'N1', 4.1, 0.0, 0.0),
    2: _player(2, 'Keeper2', 'GKP', 'N2', 3.8, 0.0, 0.0),
    10: _player(10, 'SharedDef1', 'DEF', 'N3', 4.2, 0.08, 0.06, 6.0),
    11: _player(11, 'SharedDef2', 'DEF', 'N4', 4.2, 0.08, 0.06, 6.0),
    12: _player(12, 'SharedDef3', 'DEF', 'N1', 4.1, 0.08, 0.06, 6.0),
    20: _player(20, 'SharedMid1', 'MID', 'N2', 4.6, 0.25, 0.18, 3.0),
    21: _player(21, 'SharedMid2', 'MID', 'N3', 4.6, 0.25, 0.18, 3.0),
    22: _player(22, 'SharedMid3', 'MID', 'N4', 4.5, 0.25, 0.18, 3.0),
    30: _player(30, 'SharedFwd1', 'FWD', 'N1', 5.0, 0.50, 0.08),
    31: _player(31, 'SharedFwd2', 'FWD', 'N2', 5.0, 0.50, 0.08),
    32: _player(32, 'SharedFwd3', 'FWD', 'N3', 4.8, 0.45, 0.08),
    # Strong and weak alternatives complete 5 DEF / 5 MID.
    13: _player(13, 'StrongDef1', 'DEF', 'CHE', 6.0, 0.22, 0.15, 7.0),
    14: _player(14, 'StrongDef2', 'DEF', 'LIV', 6.0, 0.22, 0.15, 7.0),
    23: _player(23, 'StrongMid1', 'MID', 'CHE', 7.0, 0.50, 0.32, 3.0),
    24: _player(24, 'StrongMid2', 'MID', 'LIV', 7.0, 0.50, 0.32, 3.0),
    15: _player(15, 'WeakDef1', 'DEF', 'SUN', 2.4, 0.02, 0.01, 3.0, 0.88),
    16: _player(16, 'WeakDef2', 'DEF', 'IPS', 2.4, 0.02, 0.01, 3.0, 0.88),
    25: _player(25, 'WeakMid1', 'MID', 'SUN', 2.2, 0.05, 0.04, 2.0, 0.88),
    26: _player(26, 'WeakMid2', 'MID', 'IPS', 2.2, 0.05, 0.04, 2.0, 0.88),
}

SHARED = [1, 2, 10, 11, 12, 20, 21, 22, 30, 31, 32]
STRONG = [13, 14, 23, 24]
WEAK = [15, 16, 25, 26]
SQUAD_A = SHARED + WEAK
SQUAD_B = SHARED + STRONG


def _run(a, b, **overrides):
    kwargs = dict(n_sims=N_SIMS, seed=SEED)
    kwargs.update(overrides)
    return compare(a, b, PLAYERS, FIXTURE_XG, START_GW, HORIZON, **kwargs)


class DecisionSimTests(unittest.TestCase):
    def test_fixed_seed_is_deterministic(self):
        first = _run(SQUAD_A, SQUAD_B)
        second = _run(SQUAD_A, SQUAD_B)
        self.assertEqual(first, second)
        self.assertEqual(
            set(first),
            {'p_b_wins', 'mean_delta', 'p_delta_gt_2', 'p_delta_lt_minus_2'},
        )
        for key in ('p_b_wins', 'p_delta_gt_2', 'p_delta_lt_minus_2'):
            self.assertTrue(0.0 <= first[key] <= 1.0, key)

    def test_strictly_stronger_squad_wins(self):
        result = _run(SQUAD_A, SQUAD_B)
        self.assertGreater(result['p_b_wins'], 0.70)
        self.assertGreater(result['mean_delta'], 0.0)
        self.assertGreater(result['p_delta_gt_2'], result['p_delta_lt_minus_2'])

    def test_identical_squads_are_exactly_half(self):
        result = _run(SQUAD_A, SQUAD_A)
        self.assertEqual(result['p_b_wins'], 0.5)
        self.assertEqual(result['mean_delta'], 0.0)
        self.assertEqual(result['p_delta_gt_2'], 0.0)
        self.assertEqual(result['p_delta_lt_minus_2'], 0.0)

    def test_requires_exactly_fifteen_players(self):
        with self.assertRaisesRegex(ValueError, r'exactly 15'):
            _run(SQUAD_A[:-1], SQUAD_B)

    def test_unknown_player_id_raises(self):
        invalid = SQUAD_A[:-1] + [999]
        with self.assertRaisesRegex(ValueError, 'not in players_by_id'):
            _run(invalid, SQUAD_B)

    def test_position_counts_raise(self):
        invalid = SQUAD_A.copy()
        invalid[invalid.index(16)] = 23  # replace a DEF with a MID
        with self.assertRaisesRegex(ValueError, 'position counts'):
            _run(invalid, SQUAD_B)

    def test_more_than_three_from_one_club_raises(self):
        players = {pid: dict(player) for pid, player in PLAYERS.items()}
        for pid in (1, 2, 10, 11):
            players[pid]['team'] = 'N1'
        with self.assertRaisesRegex(ValueError, 'at most 3 players per club'):
            compare(SQUAD_A, SQUAD_B, players, FIXTURE_XG,
                    START_GW, HORIZON, n_sims=10, seed=SEED)

    def test_opponents_share_the_same_goal_draws(self):
        fixtures = {
            'A': {1: [(2.0, 0.7, 'B')]},
            'B': {1: [(0.7, 2.0, 'A')]},
        }
        gf, ga, _, _, _ = _team_draws(
            {'A', 'B'}, fixtures, 1, 1, 200, np.random.default_rng(SEED))
        np.testing.assert_array_equal(gf['A'], ga['B'])
        np.testing.assert_array_equal(ga['A'], gf['B'])

    def test_cameo_counts_as_an_appearance(self):
        player = _player(90, 'Cameo', 'MID', 'N1', 1.0, 0.0, 0.0, start=0.0)
        player['play_by_gw'] = [1.0, 1.0]
        par = _player_params(player, 1, 1)
        gf = {'N1': np.zeros((20, 1), dtype=np.int16)}
        ga = {'N1': np.ones((20, 1), dtype=np.int16)}
        pts, played = _simulate_player(
            par, gf, ga, {'N1': np.ones(1)}, {'N1': np.ones(1, int)},
            np.random.default_rng(SEED),
        )
        self.assertTrue(played.all())
        np.testing.assert_array_equal(pts, np.ones((20, 1), dtype=np.float32))

    def test_under_sixty_minutes_gets_no_clean_sheet(self):
        player = _player(91, 'ShortStart', 'DEF', 'N1', 1.0, 0.0, 0.0)
        player['availability_by_gw'][0]['start_minutes'] = 59.0
        player['start_by_gw'] = [1.0, 1.0]
        player['play_by_gw'] = [1.0, 1.0]
        player['dc90'] = player['bonus90'] = player['yellow90'] = 0.0
        par = _player_params(player, 1, 1)
        gf = {'N1': np.zeros((20, 1), dtype=np.int16)}
        ga = {'N1': np.zeros((20, 1), dtype=np.int16)}
        pts, _ = _simulate_player(
            par, gf, ga, {'N1': np.ones(1)}, {'N1': np.ones(1, int)},
            np.random.default_rng(SEED),
        )
        np.testing.assert_array_equal(pts, np.ones((20, 1), dtype=np.float32))

    def test_vice_captain_takes_over_when_captain_misses_out(self):
        positions = ['GKP'] + ['DEF'] * 3 + ['MID'] * 4 + ['FWD'] * 3
        pars = [
            {'id': i, 'pos': pos, 'pos_i': POS_IX[pos]}
            for i, pos in enumerate(positions)
        ]
        lineup = {
            'xi': list(range(11)), 'bench': [], 'captain': 6, 'vice': 7,
        }
        pts = {i: np.zeros((1, 1), np.float32) for i in range(11)}
        played = {i: np.ones((1, 1), bool) for i in range(11)}
        played[6][0, 0] = False
        pts[7][0, 0] = 5.0
        total = _squad_gw_points(pars, lineup, pts, played, 0)
        np.testing.assert_array_equal(total, np.array([10.0], np.float32))

    def test_like_for_like_autosub_preserves_minimum_formation(self):
        positions = (['GKP'] + ['DEF'] * 3 + ['MID'] * 4
                     + ['FWD'] * 3 + ['DEF'])
        pars = [
            {'id': i, 'pos': pos, 'pos_i': POS_IX[pos]}
            for i, pos in enumerate(positions)
        ]
        lineup = {
            'xi': list(range(11)), 'bench': [11], 'captain': 6, 'vice': 7,
        }
        pts = {i: np.zeros((1, 1), np.float32) for i in range(12)}
        played = {i: np.ones((1, 1), bool) for i in range(12)}
        # XI has exactly three defenders (ids 1-3); id 1 misses out and the
        # bench defender id 11 must be allowed to replace him like-for-like.
        played[1][0, 0] = False
        pts[11][0, 0] = 6.0
        total = _squad_gw_points(pars, lineup, pts, played, 0)
        np.testing.assert_array_equal(total, np.array([6.0], np.float32))


if __name__ == '__main__':
    unittest.main()
