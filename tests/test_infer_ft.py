import sys
import unittest
from pathlib import Path

V2 = Path(__file__).resolve().parents[1] / "v2"
sys.path.insert(0, str(V2))
from weekly import MAX_FT, infer_free_transfers  # noqa: E402


def _history(chips=(), transfers=()):
    """Synthetic public entry history in the shape weekly.py reads:
    chips = [(event, name)], transfers = [(event, event_transfers)]."""
    return {
        "chips": [{"event": e, "name": n} for e, n in chips],
        "current": [{"event": e, "event_transfers": t} for e, t in transfers],
    }


class InferFreeTransfersTests(unittest.TestCase):
    def test_gameweek_1_is_unlimited(self):
        self.assertEqual(infer_free_transfers(_history(), 1), 15)
        self.assertEqual(infer_free_transfers(None, 1), 15)

    def test_normal_weeks_accrue_one_each(self):
        # GW2 spends the single starting FT; GW3-4 idle -> 1, 2, 3 at each
        # subsequent deadline.
        hist = _history(transfers=[(2, 1), (3, 0), (4, 0)])
        self.assertEqual(infer_free_transfers(hist, 5), 3)

    def test_wildcard_mid_history_preserves_bank_and_adds_nothing(self):
        # WC in GW3 with ~11 transfers made: neither spend nor gain, so the
        # one banked FT entering GW3 is still there afterwards (old code
        # credited +1 here and returned 3).
        hist = _history(
            chips=[(3, "wildcard")],
            transfers=[(2, 1), (3, 11), (4, 0)],
        )
        self.assertEqual(infer_free_transfers(hist, 5), 2)

    def test_free_hit_mid_history_preserves_bank_and_adds_nothing(self):
        hist = _history(
            chips=[(3, "freehit")],
            transfers=[(2, 1), (3, 11), (4, 0)],
        )
        self.assertEqual(infer_free_transfers(hist, 5), 2)

    def test_post_wildcard_resumes_plus_one_accrual(self):
        # After the preserved GW3 wildcard, idle GW4 and GW5 accrue +1 each.
        hist = _history(
            chips=[(3, "wildcard")],
            transfers=[(2, 1), (3, 11), (4, 0), (5, 0)],
        )
        self.assertEqual(infer_free_transfers(hist, 6), 3)

    def test_accrual_capped_at_max_ft(self):
        hist = _history(transfers=[(g, 0) for g in range(2, 10)])
        self.assertEqual(infer_free_transfers(hist, 10), MAX_FT)


if __name__ == "__main__":
    unittest.main()
