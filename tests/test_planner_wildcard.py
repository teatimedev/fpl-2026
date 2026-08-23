"""Contract P1: plan(wildcard_week=W) models the wildcard chip.

The synthetic pool alternates halves of each position between odd and even
gameweek peaks, so the optimal squad flips completely between consecutive
weeks — giving the transfer accounting deterministic pressure to inspect.
"""
import sys
import unittest
from pathlib import Path

V2 = Path(__file__).resolve().parents[1] / "v2"
sys.path.insert(0, str(V2))
from planner import MAX_BANK, plan  # noqa: E402

SHAPE = [('GKP', 2), ('DEF', 5), ('MID', 5), ('FWD', 3)]


def make_pool():
    """30 players: an A half peaking on odd gameweeks, a B half on even."""
    players = {}
    a_ids, b_ids = {}, {}
    pid = 0
    for pos, n in SHAPE:
        a_ids[pos] = []
        b_ids[pos] = []
        for kind, pts in (('A', lambda g: 8.0 if g % 2 else 0.0),
                          ('B', lambda g: 0.0 if g % 2 else 8.0)):
            for _ in range(n):
                pid += 1
                players[pid] = {
                    'id': pid, 'name': f'{kind}{pid}', 'pos': pos,
                    'price': 4.5, 'team': f'T{pid}', 'status': 'a',
                    'proj_by_gw': [pts(g) for g in range(1, 17)],
                    # Non-zero DNP risk gives the MILP's linear bench proxy a
                    # positive weight, so replacing even bench players is
                    # worth real objective mass on a wildcard week.
                    'play_by_gw': [0.9] * 16,
                }
                (a_ids if kind == 'A' else b_ids)[pos].append(pid)
    owned = []
    for pos, n in SHAPE:
        owned += a_ids[pos][:(n + 1) // 2]
        owned += b_ids[pos][:n // 2]
    return players, sorted(owned)


class WildcardPlannerTests(unittest.TestCase):
    def test_default_none_behaviour_unchanged(self):
        # Without wildcard_week the signature and behaviour are exactly as
        # before: a feasible path over the whole window exists.
        players, owned = make_pool()
        res = plan(players, owned, 0.0, 1, 7, 9)
        self.assertIsNotNone(res)
        self.assertEqual([w['gw'] for w in res['weeks']], [7, 8, 9])
        self.assertEqual(res['gw'], 7)
        self.assertEqual(res['hits'], sum(w['hits'] for w in res['weeks']))
        self.assertTrue(all(w['hits'] >= 0 for w in res['weeks']))

    def test_wildcard_first_week_makes_many_free_outs_and_preserves_bank(self):
        players, owned = make_pool()
        res = plan(players, owned, 0.0, 1, 7, 10, wildcard_week=7)
        self.assertIsNotNone(res)
        wc, nxt = res['weeks'][0], res['weeks'][1]
        self.assertEqual(wc['gw'], 7)
        # Unlimited free transfers: far more than 5 outs, zero point cost.
        self.assertGreater(len(wc['out']), 5)
        self.assertEqual(wc['hits'], 0)
        # The seeded bank enters W untouched ...
        self.assertEqual(wc['ft'], 1)
        # ... and leaves as preserved bank plus one, capped at MAX_BANK.
        self.assertEqual(nxt['ft'], min(MAX_BANK, wc['ft'] + 1))

    def test_wildcard_mid_window_preserves_bank_into_next_week(self):
        players, owned = make_pool()
        res = plan(players, owned, 0.0, 1, 7, 10, wildcard_week=8)
        self.assertIsNotNone(res)
        i = next(k for k, w in enumerate(res['weeks']) if w['gw'] == 8)
        wc = res['weeks'][i]
        self.assertEqual(wc['hits'], 0)
        self.assertGreater(len(wc['out']), 5)
        self.assertEqual(res['weeks'][i + 1]['ft'],
                         min(MAX_BANK, wc['ft'] + 1))

    def test_wildcard_conflicts_with_preseason_window(self):
        # GW1's unlimited-free pre-season rule and wildcard accounting would
        # both govern the same solve; refuse instead of picking silently.
        players, owned = make_pool()
        with self.assertRaises(ValueError):
            plan(players, owned, 0.0, 15, 1, 3, wildcard_week=1)

    def test_wildcard_outside_window_rejected(self):
        players, owned = make_pool()
        with self.assertRaises(ValueError):
            plan(players, owned, 0.0, 1, 7, 9, wildcard_week=6)


if __name__ == "__main__":
    unittest.main()
