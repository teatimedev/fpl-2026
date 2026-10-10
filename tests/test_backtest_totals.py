import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v2"))

import backtest_totals as BT  # noqa: E402


def cohort(n=8, total=76.0, rate=38.0, pts=76):
    totals = {f'holdout-{i}': dict(code=i, pos='MID', total=total, attack=rate, rate=rate)
              for i in range(n)}
    rows = {i: {'2022/23': dict(mins=2500, pts=pts)} for i in range(n)}
    return totals, rows


class CalibrateTests(unittest.TestCase):
    def test_two_season_anchor_with_one_training_season_still_applies_k(self):
        totals, rows = cohort(total=76.0, pts=95)      # projects 2.0/38, delivered 2.5
        ks = BT.calibrate(totals, rows, '2023/24', two_season=True)
        self.assertAlmostEqual(ks['MID'], 1.25)
        self.assertAlmostEqual(totals['holdout-0']['total'], 95.0)

    def test_rate_only_scope_moves_only_the_rate_part(self):
        totals, rows = cohort(total=76.0, rate=38.0, pts=95)
        ks = BT.calibrate(totals, rows, '2023/24', rate_only=True)
        # 2.5 = 1.0 fixed + k * 1.0 rate per match
        self.assertAlmostEqual(ks['MID'], 1.45)            # clipped from 1.5
        self.assertAlmostEqual(totals['holdout-0']['total'], 38.0 + 1.45 * 38.0)


if __name__ == '__main__':
    unittest.main()
