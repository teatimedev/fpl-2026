"""Replay bookkeeping: FPL's selling rule and a legal opening squad."""
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v2'))
from season_replay import opening_squad, sell_price  # noqa: E402


class ReplayBookkeepingTests(unittest.TestCase):
    def test_selling_price_keeps_half_a_rise_rounded_down_and_all_of_a_fall(self):
        self.assertEqual(sell_price(now=63, bought=60), 61)
        self.assertEqual(sell_price(now=64, bought=60), 62)
        self.assertEqual(sell_price(now=57, bought=60), 57)
        self.assertEqual(sell_price(now=60, bought=60), 60)

    def test_opening_squad_is_fifteen_legal_players_within_club_limits(self):
        players = {}
        for pos, n in (('GKP', 6), ('DEF', 12), ('MID', 12), ('FWD', 8)):
            for k in range(n):
                pid = len(players) + 1
                players[pid] = dict(id=pid, pos=pos, team=f'T{pid % 8}', price=4.0 + k / 10,
                                    proj_by_gw=[1.0] * 38)
        squad = opening_squad(players)
        self.assertEqual(len(squad), 15)
        self.assertEqual(Counter(players[i]['pos'] for i in squad),
                         Counter(GKP=2, DEF=5, MID=5, FWD=3))
        self.assertLessEqual(max(Counter(players[i]['team'] for i in squad).values()), 3)


if __name__ == '__main__':
    unittest.main()
