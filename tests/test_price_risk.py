"""price_risk: snapshot parsing, momentum, heuristic risk tiers, advisory.

The heuristic coefficients are placeholders by design (see the module
docstring), so these tests pin the MECHANISM, not calibrated numbers:
quartile-scaled thresholds, monotone probabilities, reset-aware flows,
tier cuts, and the advisory wording the planner would one day surface.
"""
import csv
import tempfile
import unittest
from pathlib import Path

from v2.price_risk import (
    P_MAX,
    P_MIN,
    advisory,
    load_deadlines,
    load_snapshots,
    momentum,
    risk,
)

HEADER = ['id', 'price', 'sel_pct', 'tin', 'tout', 'dcost_event', 'status']


def write_day(tmp, date, rows):
    """One dated snapshot CSV; `rows` are dicts with id/price/sel/tin/tout."""
    path = Path(tmp) / f'{date}.csv'
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for r in rows:
            w.writerow([r['id'], r['price'], r['sel'], r['tin'], r['tout'],
                        r.get('dcost', 0), r.get('status', 'a')])
    return path


def row(pid, price, sel, tin, tout, **kw):
    return dict(id=pid, price=price, sel=sel, tin=tin, tout=tout, **kw)


def scenario(tmp):
    """Three snapshots, eight players. Written out of date order on purpose
    so load_snapshots' sort is exercised. sel_pct quartile cuts among the
    owned (>=1%) players in the final snapshot are [5.5, 12.0, 22.5]:
      1 riser_light sel 2.0  -> Q0, threshold 40k
      2 riser_heavy sel 30.0 -> Q3, threshold 150k
      3 moderate     sel 20.0 -> Q2, threshold 113.3k
      4 stable       sel 12.0 -> Q2, threshold 113.3k
      5 faller       sel 25.0 -> Q3, threshold 150k
      6 absent_old   sel 3.0  -> Q0, appears ONLY in the last snapshot
      7 fodder       sel 0.5  -> Q0 by the ownership floor
      8 filler       sel 8.0  -> Q1, never moves
    Snapshot 3 restarts tin/tout (new gameweek) for every player, so all
    d2->d3 flows take the reset-aware branch.
    """
    write_day(tmp, '2026-09-02', [
        row(1, 4.6, 2.0, 48000, 2000),
        row(2, 7.2, 30.0, 220000, 20000, dcost=2),
        row(3, 5.0, 20.0, 85000, 5000),
        row(4, 6.0, 12.0, 20000, 20000),
        row(5, 6.3, 25.0, 5000, 180000),
        row(6, 5.5, 3.0, 200000, 10000),
        row(7, 4.0, 0.5, 100, 50),
        row(8, 4.5, 8.0, 10000, 10000),
    ])
    write_day(tmp, '2026-08-30', [
        row(1, 4.5, 2.0, 50000, 5000),
        row(2, 7.1, 30.0, 500000, 150000),
        row(3, 5.0, 20.0, 140000, 60000),
        row(4, 6.0, 12.0, 45000, 45000),
        row(5, 6.4, 25.0, 15000, 260000),
        row(7, 4.0, 0.5, 80, 30),
        row(8, 4.5, 8.0, 8000, 8000),
    ])
    write_day(tmp, '2026-08-28', [
        row(1, 4.5, 2.0, 20000, 5000),
        row(2, 7.0, 30.0, 250000, 100000),
        row(3, 5.0, 20.0, 60000, 60000),
        row(4, 6.0, 12.0, 30000, 30000),
        row(5, 6.5, 25.0, 10000, 60000),
        row(7, 4.0, 0.5, 50, 20),
        row(8, 4.5, 8.0, 5000, 5000),
    ])
    return load_snapshots(tmp)


class LoadSnapshotsTests(unittest.TestCase):
    def test_sorts_by_date_and_parses_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_day(tmp, '2026-08-30', [row(1, 4.5, 2.0, 10, 5)])
            # A malformed row (bad id) and a short row must be skipped.
            with open(path, 'a', newline='') as f:
                f.write('not_an_id,4.0,1.0,0,0,0,a\n')
                f.write('99,4.0\n')
            (Path(tmp) / 'README.txt').write_text('not a snapshot')
            write_day(tmp, '2026-08-28', [row(2, 5.0, 3.0, 0, 0)])

            snaps = load_snapshots(tmp)

        self.assertEqual([s['date'] for s in snaps],
                         ['2026-08-28', '2026-08-30'])
        self.assertEqual(set(snaps[1]['rows']), {1})
        r = snaps[1]['rows'][1]
        self.assertIsInstance(r['price'], float)
        self.assertIsInstance(r['tin'], int)
        self.assertEqual(r['status'], 'a')

    def test_empty_directory_gives_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_snapshots(tmp), [])


class MomentumTests(unittest.TestCase):
    def test_rising_player_has_positive_flows_and_price_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            snaps = scenario(tmp)
            m = momentum(snaps, 2)  # riser_heavy
        self.assertEqual(m['n'], 3)
        self.assertEqual(m['prices'],
                         [('2026-08-28', 7.0), ('2026-08-30', 7.1),
                          ('2026-09-02', 7.2)])
        self.assertAlmostEqual(m['price_delta'], 0.2)
        self.assertEqual([f[2] for f in m['flows']], [200000, 200000])
        self.assertEqual(
            [(d0, d1) for d0, d1, _ in m['price_changes']],
            [('2026-08-28', '2026-08-30'),
             ('2026-08-30', '2026-09-02')],
        )
        self.assertTrue(
            all(abs(delta - 0.1) < 1e-12
                for _, _, delta in m['price_changes']),
        )
        self.assertEqual(m['net_event'], 200000)

    def test_flow_is_reset_aware_across_a_gameweek_boundary(self):
        # riser_heavy's event net goes +350k -> +200k because tin/tout
        # restarted at the deadline. The naive delta (-150k) would flip the
        # sign and read as selling; the flow must stay positive.
        with tempfile.TemporaryDirectory() as tmp:
            snaps = scenario(tmp)
            m = momentum(snaps, 2)
        self.assertEqual(m['flows'][1],
                         ('2026-08-30', '2026-09-02', 200000))

    def test_reset_detected_even_when_the_new_events_volume_exceeds_the_old_sum(self):
        # Real shape from the 2026-09-02 log (João Pedro): tin DECREASED
        # 315,886 -> 300,243 across the GW2->GW3 deadline while tout grew,
        # so the counter SUM grew and a sum-based reset check misread the
        # boundary as -78k of selling. Any decreasing counter is the reset.
        with tempfile.TemporaryDirectory() as tmp:
            write_day(tmp, '2026-08-30', [
                row(1, 7.7, 30.0, 300243, 85433),
                row(2, 5.0, 2.0, 0, 0),
                row(3, 5.0, 12.0, 0, 0),
            ])
            write_day(tmp, '2026-08-27', [
                row(1, 7.7, 30.0, 315886, 23377),
                row(2, 5.0, 2.0, 0, 0),
                row(3, 5.0, 12.0, 0, 0),
            ])
            snaps = load_snapshots(tmp)
            m = momentum(snaps, 1)
            r = risk(snaps, {'id': 1})
        self.assertEqual(m['flows'], [('2026-08-27', '2026-08-30', 214810)])
        self.assertEqual(r['tier'], 'high')
        self.assertNotIn('downgraded', r['evidence'])

    def test_falling_player_has_negative_flows(self):
        with tempfile.TemporaryDirectory() as tmp:
            snaps = scenario(tmp)
            m = momentum(snaps, 5)  # faller
        self.assertEqual([f[2] for f in m['flows']], [-195000, -175000])
        self.assertAlmostEqual(m['price_delta'], -0.2)

    def test_player_absent_from_old_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            snaps = scenario(tmp)
            m = momentum(snaps, 6)  # only in the final snapshot
        self.assertEqual(m['n'], 1)
        self.assertEqual(m['prices'], [('2026-09-02', 5.5)])
        self.assertEqual(m['price_changes'], [])
        self.assertEqual(m['flows'], [])
        self.assertIsNone(m['price_delta'])
        self.assertIsNone(m['flow_latest'])
        self.assertEqual(m['net_event'], 190000)

    def test_player_absent_everywhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            snaps = scenario(tmp)
            m = momentum(snaps, 999)
        self.assertEqual(m['n'], 0)
        self.assertEqual(m['prices'], [])
        self.assertIsNone(m['net_event'])


class RiskTests(unittest.TestCase):
    def test_tiers_rise_watch_fall(self):
        with tempfile.TemporaryDirectory() as tmp:
            snaps = scenario(tmp)
            self.assertEqual(risk(snaps, {'id': 2})['tier'], 'high')
            self.assertEqual(risk(snaps, {'id': 1})['tier'], 'high')
            self.assertEqual(risk(snaps, {'id': 3})['tier'], 'watch')
            self.assertEqual(risk(snaps, {'id': 4})['tier'], 'low')
            self.assertEqual(risk(snaps, {'id': 5})['tier'], 'high')
            self.assertEqual(risk(snaps, {'id': 6})['tier'], 'high')

    def test_p_rise_orders_with_net_in_pressure(self):
        with tempfile.TemporaryDirectory() as tmp:
            snaps = scenario(tmp)
            p_heavy = risk(snaps, {'id': 2})['p_rise']     # r = 1.33
            p_light = risk(snaps, {'id': 1})['p_rise']     # r = 1.15
            p_mod = risk(snaps, {'id': 3})['p_rise']       # r = 0.71
            p_stable = risk(snaps, {'id': 4})['p_rise']    # r = 0
        self.assertEqual(p_heavy, P_MAX)  # saturates at the clamp
        self.assertEqual(p_light, P_MAX)
        self.assertGreater(p_mod, p_stable)
        self.assertLess(p_mod, P_MAX)
        self.assertGreater(p_stable, P_MIN)

    def test_fall_risk_mirrors_rise(self):
        with tempfile.TemporaryDirectory() as tmp:
            snaps = scenario(tmp)
            r = risk(snaps, {'id': 5})  # faller: r_fall = 175/150
        self.assertEqual(r['p_fall'], P_MAX)
        self.assertLess(r['p_rise'], 0.10)
        self.assertEqual(r['tier'], 'high')

    def test_heavily_owned_needs_more_net_than_lightly_owned(self):
        # Both saturate, but via the thresholds: riser_light's +46k net would
        # be nowhere near a move at riser_heavy's 150k threshold, while the
        # stable player at the SAME ownership band as moderate sits at low.
        with tempfile.TemporaryDirectory() as tmp:
            snaps = scenario(tmp)
            self.assertIn('~40k to move', risk(snaps, {'id': 1})['evidence'])
            self.assertIn('~150k to move', risk(snaps, {'id': 2})['evidence'])

    def test_evidence_carries_the_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            snaps = scenario(tmp)
            ev = risk(snaps, {'id': 2})['evidence']
        self.assertIn('net +200k this event', ev)
        self.assertIn('sel 30.0% = Q3', ev)
        self.assertIn('(+0.2m in window)', ev)
        self.assertIn('already +2x0.1m this event', ev)

    def test_player_missing_everywhere_is_low_with_floor_probabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            snaps = scenario(tmp)
            r = risk(snaps, {'id': 999})
        self.assertEqual(r['p_rise'], P_MIN)
        self.assertEqual(r['p_fall'], P_MIN)
        self.assertEqual(r['tier'], 'low')
        self.assertIn('not in the latest snapshot', r['evidence'])

    def test_reversed_latest_flow_downgrades_the_tier(self):
        # Big positive event net, but the most recent between-snapshot flow
        # turned negative: FPL moves on sustained pressure, so high -> watch.
        with tempfile.TemporaryDirectory() as tmp:
            write_day(tmp, '2026-08-30', [
                row(1, 5.0, 2.0, 110000, 20000),   # net +90k, flow -10k
                row(2, 5.0, 30.0, 200000, 0),      # net +200k, flow +200k
                row(3, 5.0, 12.0, 0, 0),
            ])
            write_day(tmp, '2026-08-28', [
                row(1, 5.0, 2.0, 100000, 0),       # net +100k
                row(2, 5.0, 30.0, 0, 0),
                row(3, 5.0, 12.0, 0, 0),
            ])
            snaps = load_snapshots(tmp)
            reversing = risk(snaps, {'id': 1})
            control = risk(snaps, {'id': 2})
        self.assertEqual(reversing['tier'], 'watch')
        self.assertIn('downgraded', reversing['evidence'])
        self.assertEqual(control['tier'], 'high')

    def test_single_snapshot_still_ranks(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_day(tmp, '2026-09-02', [
                row(1, 4.5, 2.0, 60000, 0),
                row(2, 5.0, 12.0, 10, 10),
            ])
            snaps = load_snapshots(tmp)
            self.assertEqual(risk(snaps, {'id': 1})['tier'], 'high')
            self.assertEqual(risk(snaps, {'id': 2})['tier'], 'low')


class AdvisoryTests(unittest.TestCase):
    def test_rise_risk_target_and_fall_risk_squad_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            snaps = scenario(tmp)
            lines = advisory(
                snaps,
                squad_rows=[{'id': 5, 'name': 'Dropper'},
                            {'id': 4, 'name': 'Steady'}],
                target_rows=[{'id': 2, 'name': 'Cherki'},
                             {'id': 4, 'name': 'Steady'}])
        self.assertEqual(len(lines), 2)
        target_line, squad_line = lines
        self.assertIn('Target Cherki (net +200k this event)', target_line)
        self.assertIn('rise risk', target_line)
        self.assertIn('+0.1m', target_line)
        self.assertIn('Squad player Dropper', squad_line)
        self.assertIn('fall risk', squad_line)
        self.assertIn('-0.1m', squad_line)

    def test_falling_target_gets_the_no_rush_reading(self):
        with tempfile.TemporaryDirectory() as tmp:
            snaps = scenario(tmp)
            lines = advisory(snaps, squad_rows=[],
                             target_rows=[{'id': 5, 'name': 'Dropper'}])
        self.assertEqual(len(lines), 1)
        self.assertIn('no rush', lines[0])
        self.assertIn('Target Dropper', lines[0])

    def test_rising_squad_player_is_silent(self):
        # A squad player RISING costs nothing; only falls hit the bank.
        with tempfile.TemporaryDirectory() as tmp:
            snaps = scenario(tmp)
            lines = advisory(snaps, squad_rows=[{'id': 2, 'name': 'Cherki'}],
                             target_rows=[])
        self.assertEqual(lines, [])

    def test_all_low_gives_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            snaps = scenario(tmp)
            self.assertEqual(advisory(snaps,
                                      squad_rows=[{'id': 4, 'name': 'S'}],
                                      target_rows=[{'id': 8, 'name': 'T'}]),
                             [])

    def test_unknown_player_and_empty_log_give_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            snaps = scenario(tmp)
            self.assertEqual(advisory(snaps, squad_rows=[],
                                      target_rows=[{'id': 999, 'name': 'X'}]),
                             [])
        self.assertEqual(advisory([], squad_rows=[{'id': 1}],
                                  target_rows=[{'id': 1}]), [])


class DeadlineResetTests(unittest.TestCase):
    """The counter rule alone cannot see a restart when the new event's
    volume already exceeds the previous snapshot's cumulative counters —
    only a deadline date between the snapshots catches it (Calafiori,
    2026-08-27 -> 2026-09-02 across the 28 Aug GW2 deadline)."""

    def _pair(self, tmp):
        write_day(tmp, '2026-09-02', [
            row(1, 5.6, 43.3, 297000, 70000),   # counters GREW; naive -12k
            row(2, 5.0, 2.0, 0, 0),
            row(3, 5.0, 12.0, 0, 0),
        ])
        write_day(tmp, '2026-08-27', [
            row(1, 5.6, 43.3, 285000, 46000),
            row(2, 5.0, 2.0, 0, 0),
            row(3, 5.0, 12.0, 0, 0),
        ])
        return load_snapshots(tmp)

    def test_deadline_between_snapshots_forces_reset_even_when_counters_grew(self):
        with tempfile.TemporaryDirectory() as tmp:
            snaps = self._pair(tmp)
            m = momentum(snaps, 1, deadlines=['2026-08-28'])
            r = risk(snaps, {'id': 1}, deadlines=['2026-08-28'])
        self.assertEqual(m['flows'],
                         [('2026-08-27', '2026-09-02', 227000)])
        self.assertEqual(r['tier'], 'high')
        self.assertNotIn('downgraded', r['evidence'])

    def test_without_deadlines_the_same_pair_falsely_reads_as_reversal(self):
        # Documents exactly what load_deadlines() buys: without it the
        # counter rule misreads this boundary as selling and downgrades.
        with tempfile.TemporaryDirectory() as tmp:
            snaps = self._pair(tmp)
            m = momentum(snaps, 1)
            r = risk(snaps, {'id': 1})
        self.assertEqual(m['flows'],
                         [('2026-08-27', '2026-09-02', -12000)])
        self.assertEqual(r['tier'], 'watch')
        self.assertIn('downgraded', r['evidence'])

    def test_deadline_on_the_earlier_snapshot_counts_as_boundary(self):
        # Inclusive at d0: a morning snapshot precedes that evening's
        # deadline. A false reset merely overcounts recency; a missed one
        # flips the sign — so the rule errs generous.
        with tempfile.TemporaryDirectory() as tmp:
            write_day(tmp, '2026-08-30', [
                row(1, 5.0, 30.0, 12000, 1000),
                row(2, 5.0, 2.0, 0, 0),
                row(3, 5.0, 12.0, 0, 0),
            ])
            write_day(tmp, '2026-08-28', [
                row(1, 5.0, 30.0, 10000, 0),
                row(2, 5.0, 2.0, 0, 0),
                row(3, 5.0, 12.0, 0, 0),
            ])
            m = momentum(load_snapshots(tmp), 1, deadlines=['2026-08-28'])
        self.assertEqual(m['flows'],
                         [('2026-08-28', '2026-08-30', 11000)])

    def test_deadline_on_the_later_snapshot_is_not_a_boundary(self):
        # The later snapshot was taken before that evening's deadline, so
        # the counters are still the old event's: plain delta applies.
        with tempfile.TemporaryDirectory() as tmp:
            write_day(tmp, '2026-08-21', [
                row(1, 5.0, 30.0, 20000, 2000),
                row(2, 5.0, 2.0, 0, 0),
                row(3, 5.0, 12.0, 0, 0),
            ])
            write_day(tmp, '2026-08-20', [
                row(1, 5.0, 30.0, 10000, 0),
                row(2, 5.0, 2.0, 0, 0),
                row(3, 5.0, 12.0, 0, 0),
            ])
            m = momentum(load_snapshots(tmp), 1, deadlines=['2026-08-21'])
        self.assertEqual(m['flows'],
                         [('2026-08-20', '2026-08-21', 8000)])

    def test_load_deadlines_reads_the_cache_and_degrades_to_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'bootstrap.json'
            cache.write_text('{"events": ['
                             '{"deadline_time": "2026-08-28T17:30:00Z"}, '
                             '{"deadline_time": "2026-08-21T17:30:00Z"}]}')
            self.assertEqual(load_deadlines(cache),
                             ['2026-08-21', '2026-08-28'])
            self.assertEqual(load_deadlines(Path(tmp) / 'nope.json'), [])


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
