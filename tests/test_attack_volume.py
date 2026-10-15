import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v2"))

from v2.attack_volume import LEAGUE_XG, attack_volume, club_mean_xg  # noqa: E402
import retro as RT  # noqa: E402


class AttackVolumeTests(unittest.TestCase):
    def test_relative_rule_scales_against_the_club_not_the_league(self):
        self.assertAlmostEqual(attack_volume(1.9, 1.9, rule='relative'), 1.0)
        self.assertAlmostEqual(attack_volume(2.0, 1.0, rule='relative'), math.sqrt(2.0))
        self.assertAlmostEqual(attack_volume(0.5, 1.0, rule='relative'), math.sqrt(0.5))

    def test_league_rule_and_missing_club_level_keep_the_old_formula(self):
        self.assertAlmostEqual(attack_volume(1.9, 1.9, rule='league'), 1.9 / LEAGUE_XG)
        self.assertAlmostEqual(attack_volume(1.9, None, rule='relative'), 1.9 / LEAGUE_XG)

    def test_club_mean_uses_every_fixture_in_the_view(self):
        view = {'MCI': {'1': [dict(xg=2.0)], '2': [dict(xg=1.0), dict(xg=1.5)], '3': []},
                'COV': {'1': [dict(xg=0.9)]}}
        self.assertEqual(club_mean_xg(view), {'MCI': 1.5, 'COV': 0.9})

    def test_retro_decomposition_follows_the_row_club_level(self):
        row = dict(pos='FWD', xg90=0.8, xa90=0.1, dc90=0.0, bonus90=0.0, saves90=0.0,
                   yellow90=0.0, evidence=0.9)
        fixtures = [dict(xg=2.2, xgc=1.0, cs=0.4)]
        old = RT.expected_components(row, fixtures, 1.0, 0.0, 90.0, 25.0, 1.0)
        new = RT.expected_components(dict(row, club_xg=2.0), fixtures, 1.0, 0.0, 90.0, 25.0, 1.0)
        self.assertAlmostEqual(old['xg'], 0.8 * 2.2 / LEAGUE_XG)
        self.assertAlmostEqual(new['xg'], 0.8 * attack_volume(2.2, 2.0))


if __name__ == '__main__':
    unittest.main()
