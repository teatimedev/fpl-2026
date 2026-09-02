"""manager_minutes: hook cells, shrinkage and the exclusion rule, on a
synthetic gw_stat built programmatically (an always-90 club, an
always-hooked-at-58 club, a one-start club)."""
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / 'v2'
sys.path.insert(0, str(V2))

import manager_minutes as MM  # noqa: E402

# the real table's DDL, verbatim — tests must mirror production
SCHEMA = """CREATE TABLE gw_stat (
        code INTEGER, season TEXT, round INTEGER, fixture_id INTEGER,
        team TEXT, pos TEXT, opponent TEXT, was_home INTEGER, kickoff TEXT,
        minutes INTEGER, starts INTEGER, points INTEGER,
        goals INTEGER, assists INTEGER, clean_sheets INTEGER,
        goals_conceded INTEGER, own_goals INTEGER,
        pens_saved INTEGER, pens_missed INTEGER,
        xg REAL, xa REAL, xgc REAL, defcon INTEGER,
        bps INTEGER, bonus INTEGER, saves INTEGER, yellow INTEGER, red INTEGER,
        threat REAL, creativity REAL, influence REAL,
        price INTEGER, selected INTEGER,
        PRIMARY KEY (code, season, fixture_id)
    );"""


def build_db(path):
    """One ever-90 club (AAA), one hooked-at-58 club (BBB), a one-start club
    (CCC), a mixed cell (DDD) for the exclusion checks, and a second season
    so cross-season pooling is exercised. All MID."""
    cx = sqlite3.connect(path)
    cx.executescript(SCHEMA)
    state = {'fid': 0}

    def start(code, season, rnd, team, minutes):
        state['fid'] += 1
        cx.execute('INSERT INTO gw_stat (code, season, round, fixture_id, team,'
                   ' pos, minutes, starts) VALUES (?,?,?,?,?,?,?,1)',
                   (code, season, rnd, state['fid'], team, 'MID', minutes))

    for code in (101, 102):
        for rnd in range(1, 13):
            start(code, '2024/25', rnd, 'AAA', 90)
    for code in (201, 202):
        for rnd in range(1, 13):
            start(code, '2024/25', rnd, 'BBB', 58)
    start(301, '2024/25', 1, 'CCC', 90)
    for rnd in range(1, 7):
        start(401, '2024/25', rnd, 'DDD', 90 if rnd % 2 else 58)
        start(402, '2024/25', rnd, 'DDD', 90)
    start(101, '2023/24', 1, 'AAA', 90)
    cx.commit()
    cx.close()


class ManagerMinutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls._tmp.name) / 'mm.db'
        build_db(cls.db)
        cls.table = MM.load_from_db(['2023/24', '2024/25'], db=cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_missing_gw_stat_disables_manager_blend_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'old.db'
            sqlite3.connect(db).close()
            table = MM.load_from_db(['2024/25'], db=db)

        self.assertEqual(table['cells'], {})
        self.assertEqual(table['league'], {})
        self.assertEqual(table['provenance']['start_rows'], 0)
        self.assertIn('unavailable', table['provenance']['source'])

    def test_table_separates_hooked_from_ever_present_clubs(self):
        aaa = self.table['cells'][('AAA', '2024/25', 'MID')]
        bbb = self.table['cells'][('BBB', '2024/25', 'MID')]
        league = self.table['league'][('2024/25', 'MID')]
        self.assertEqual(aaa['n'], 24)
        self.assertEqual(bbb['n'], 24)
        self.assertAlmostEqual(aaa['raw_mps'], 90.0)
        self.assertAlmostEqual(bbb['raw_mps'], 58.0)
        # shrinkage at trust 24/30 keeps 80% of the 32-minute raw gap
        self.assertAlmostEqual(aaa['mps'] - bbb['mps'], 0.8 * 32.0)
        self.assertGreater(aaa['mps'], league['mps'])
        self.assertLess(bbb['mps'], league['mps'])
        self.assertGreater(bbb['hook_rate'], 0.7)   # 0.8*1 + 0.2*league
        self.assertLess(aaa['hook_rate'], 0.3)
        self.assertGreater(aaa['full90'], 0.9)
        self.assertLess(bbb['full90'], 0.3)

    def test_cells_match_a_direct_sql_query_of_the_same_rows(self):
        cx = sqlite3.connect(self.db)
        for team, raw_expected, n_expected in (('AAA', 90.0, 24), ('BBB', 58.0, 24),
                                               ('DDD', 82.0, 12)):
            avg, cnt, hooked = cx.execute(
                'SELECT AVG(minutes), COUNT(*), SUM(minutes < 60) FROM gw_stat'
                ' WHERE team=? AND season=? AND pos=? AND starts>0', (team, '2024/25', 'MID')).fetchone()
            cell = self.table['cells'][(team, '2024/25', 'MID')]
            self.assertAlmostEqual(cell['raw_mps'], avg)
            self.assertEqual(cell['n'], cnt)
            self.assertEqual(cnt, n_expected)
        cx.close()

    def test_shrinkage_pulls_a_one_start_cell_most_of_the_way_to_league(self):
        ccc = self.table['cells'][('CCC', '2024/25', 'MID')]
        league = self.table['league'][('2024/25', 'MID')]['mps']
        self.assertEqual(ccc['n'], 1)
        self.assertAlmostEqual(ccc['raw_mps'], 90.0)
        # trust 1/7: the cell lands within 20% of the raw-to-league gap
        self.assertAlmostEqual(ccc['mps'], (1 / 7) * 90.0 + (6 / 7) * league)
        self.assertLess(abs(ccc['mps'] - league), 0.2 * abs(90.0 - league))

    def test_accessor_falls_back_to_league_then_pools_seasons(self):
        t = self.table
        aaa = t['cells'][('AAA', '2024/25', 'MID')]['mps']
        self.assertAlmostEqual(MM.mps_expectation(t, 'AAA', 'MID', '2024/25'), aaa)
        # unknown club, known season -> that season's league mean
        self.assertAlmostEqual(MM.mps_expectation(t, 'ZZZ', 'MID', '2024/25'),
                               t['league'][('2024/25', 'MID')]['mps'])
        # unknown season -> the club pooled n-weighted across seasons
        pooled = ((1 * t['cells'][('AAA', '2023/24', 'MID')]['mps']
                   + 24 * aaa) / 25)
        self.assertAlmostEqual(MM.mps_expectation(t, 'AAA', 'MID'), pooled)
        self.assertAlmostEqual(MM.mps_expectation(t, 'AAA', 'MID', '1999/00'), pooled)
        # unknown club and season -> pos league pooled n-weighted
        lg_pool = ((1 * t['league'][('2023/24', 'MID')]['mps']
                    + 61 * t['league'][('2024/25', 'MID')]['mps']) / 62)
        self.assertAlmostEqual(MM.mps_expectation(t, 'ZZZ', 'MID'), lg_pool)
        # unknown pos -> every cell pooled; empty table -> the hardcoded default
        w = sum(c['n'] for c in t['cells'].values())
        self.assertAlmostEqual(MM.mps_expectation(t, 'ZZZ', 'DEF', '2024/25'),
                               sum(c['mps'] * c['n'] for c in t['cells'].values()) / w)
        self.assertEqual(MM.mps_expectation({'cells': {}, 'league': {}}, 'AAA', 'MID'),
                         MM.DEFAULT_MPS)

    def test_accessor_can_remove_the_target_players_contribution(self):
        league = self.table['league'][('2024/25', 'MID')]
        cell = self.table['cells'][('DDD', '2024/25', 'MID')]
        self.assertEqual(cell['contributions'][401],
                         {'n': 6, 'minutes_sum': 444})
        without_401 = MM.mps_expectation(
            self.table, 'DDD', 'MID', '2024/25', exclude_code=401)
        without_402 = MM.mps_expectation(
            self.table, 'DDD', 'MID', '2024/25', exclude_code=402)
        league_401 = (league['mps'] * league['n'] - 444) / (league['n'] - 6)
        league_402 = (league['mps'] * league['n'] - 540) / (league['n'] - 6)
        self.assertAlmostEqual(without_401, 0.5 * 90.0 + 0.5 * league_401)
        self.assertAlmostEqual(without_402, 0.5 * 74.0 + 0.5 * league_402)
        self.assertGreater(without_401, cell['mps'])
        self.assertLess(without_402, cell['mps'])
        # Removing the sole contributor leaves the league with that player
        # removed too — the same prior the no-leak backtest uses.
        league_301 = (league['mps'] * league['n'] - 90) / (league['n'] - 1)
        self.assertAlmostEqual(
            MM.mps_expectation(
                self.table, 'CCC', 'MID', '2024/25', exclude_code=301),
            league_301)

    def test_manager_cell_excludes_own_rows_and_honours_asof_round(self):
        starts = [(1, 101, 90), (2, 101, 90), (3, 101, 90),
                  (1, 202, 58), (2, 202, 58), (3, 202, 58)]
        # player 101's own rows gone: only 202's first two rounds survive
        c = MM.manager_cell(starts, exclude_code=101, before_round=3, league_mps=74.0)
        self.assertEqual(c['n'], 2)
        self.assertAlmostEqual(c['raw_mps'], 58.0)
        self.assertAlmostEqual(c['mps'], (2 / 8) * 58.0 + (6 / 8) * 74.0)
        # nobody excluded, still as-of round 3: both players, rounds 1-2
        c = MM.manager_cell(starts, exclude_code=999, before_round=3, league_mps=74.0)
        self.assertEqual(c['n'], 4)
        self.assertAlmostEqual(c['raw_mps'], 74.0)
        # round-1 target: no earlier rows at all -> the league prior itself
        c = MM.manager_cell(starts, exclude_code=101, before_round=1, league_mps=74.0)
        self.assertEqual((c['n'], c['raw_mps'], c['mps']), (0, None, 74.0))
        # no rows and no league -> the caller's fallback; k=0 disables shrink
        c = MM.manager_cell([], league_mps=None, fallback=80.0)
        self.assertEqual((c['n'], c['mps']), (0, 80.0))
        c = MM.manager_cell([(1, 5, 60)], league_mps=90.0, k=0.0)
        self.assertAlmostEqual(c['mps'], 60.0)

    def test_exclusion_rule_matches_a_direct_sql_query(self):
        """The player's own rows must not sit in his manager cell: the cell's
        raw mean equals what SQL says about everyone BUT him, as-of included."""
        cx = sqlite3.connect(self.db)
        rows = cx.execute("SELECT round, code, minutes FROM gw_stat WHERE team='DDD'"
                          " AND season='2024/25' AND pos='MID' AND starts>0").fetchall()
        for code, before in ((401, 99), (401, 4), (402, 4)):
            cell = MM.manager_cell(rows, exclude_code=code, before_round=before,
                                   league_mps=75.0)
            avg, cnt = cx.execute(
                'SELECT AVG(minutes), COUNT(*) FROM gw_stat WHERE team=? AND'
                ' season=? AND pos=? AND starts>0 AND code != ? AND round < ?',
                ('DDD', '2024/25', 'MID', code, before)).fetchone()
            self.assertEqual(cell['n'], cnt, (code, before))
            self.assertAlmostEqual(cell['raw_mps'], avg, places=6, msg=str((code, before)))
        cx.close()

    def test_json_carries_table_and_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'mm.json'
            MM.write_json(self.table, out)
            d = json.loads(out.read_text())
            self.assertIn('generated', d['provenance'])
            self.assertEqual(d['provenance']['seasons'], ['2023/24', '2024/25'])
            self.assertEqual(d['provenance']['start_rows'], 62)
            self.assertEqual(d['provenance']['shrink_k'], MM.K)
            self.assertAlmostEqual(d['cells']['AAA|2024/25|MID']['mps'],
                                   self.table['cells'][('AAA', '2024/25', 'MID')]['mps'])
            self.assertAlmostEqual(d['league']['2024/25|MID']['mps'],
                                   self.table['league'][('2024/25', 'MID')]['mps'])
            contribution = d['cells']['DDD|2024/25|MID']['contributions']['401']
            self.assertEqual(contribution, {'n': 6, 'minutes_sum': 444})
            loaded = MM.read_json(out)
            self.assertEqual(
                loaded['cells'][('DDD', '2024/25', 'MID')]['contributions'][401],
                contribution)
            self.assertAlmostEqual(
                MM.mps_expectation(
                    loaded, 'DDD', 'MID', '2024/25', exclude_code=401),
                MM.mps_expectation(
                    self.table, 'DDD', 'MID', '2024/25', exclude_code=401))

    def test_cli_module_writes_the_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'mm.json'
            r = subprocess.run([sys.executable, '-m', 'v2.manager_minutes',
                                '--db', str(self.db), '--out', str(out)],
                               cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            d = json.loads(out.read_text())
            self.assertEqual(d['provenance']['start_rows'], 62)
            self.assertIn('BBB|2024/25|MID', d['cells'])


if __name__ == '__main__':
    unittest.main()
