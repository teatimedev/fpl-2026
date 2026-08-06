"""
What actually predicts next season?

Before choosing how much to trust last season's numbers, measure it. This runs
four studies on the 2022/23-2025/26 player panel and the answers set the
parameters of the v2 model rather than being guessed:

  1. Year-over-year stability of each per-90 metric. A metric that correlates
     0.75 with itself next season deserves far more weight than one at 0.25,
     and that ratio is exactly what an empirical-Bayes shrinkage needs.

  2. Does xG predict next season's goals better than goals do? The received
     wisdom in football analytics is yes. It is worth checking on FPL's own
     data rather than assuming.

  3. Is a player's SHARE of his team's attacking output more stable than his
     absolute rate? This is the premise of the share x volume approach -- if it
     is false, the whole design is wrong and should be abandoned.

  4. Aging: how per-90 output moves with age, estimated from the panel.

Everything is minutes-weighted, because a 300-minute season is mostly noise.
"""
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'v2' / 'fpl.db'
SEASONS = ['2022/23', '2023/24', '2024/25', '2025/26']
MIN_MINS = 900


def load_panel():
    cx = sqlite3.connect(DB)
    rows = cx.execute("""
        SELECT s.code, s.season, s.minutes, s.starts, s.points, s.goals, s.assists,
               s.xg, s.xa, s.xgc, s.defcon, s.clean_sheets, s.bonus, s.bps,
               p.pos, p.birth_date, p.web_name, p.team
        FROM season_stat s JOIN player p ON p.code = s.code
        WHERE s.season IN ('2022/23','2023/24','2024/25','2025/26')
    """).fetchall()
    cx.close()
    out = defaultdict(dict)
    for (code, season, mins, starts, pts, g, a, xg, xa, xgc, dc, cs, bonus,
         bps, pos, dob, name, team) in rows:
        if not mins:
            continue
        p90 = mins / 90.0
        out[code][season] = dict(
            code=code, season=season, mins=mins, starts=starts or 0, pos=pos,
            name=name, dob=dob, team=team,
            pts90=pts / p90, g90=g / p90, a90=a / p90,
            xg90=(xg or 0) / p90, xa90=(xa or 0) / p90,
            xgi90=((xg or 0) + (xa or 0)) / p90,
            xgc90=(xgc or 0) / p90, dc90=(dc or 0) / p90,
            cs90=(cs or 0) / p90, bonus90=(bonus or 0) / p90,
            bps90=(bps or 0) / p90,
            g=g, a=a, xg=xg or 0, xa=xa or 0, dc=dc or 0,
        )
    return out


def wcorr(x, y, w):
    """Minutes-weighted Pearson correlation."""
    x, y, w = np.asarray(x, float), np.asarray(y, float), np.asarray(w, float)
    if len(x) < 8:
        return float('nan'), len(x)
    mx = np.average(x, weights=w)
    my = np.average(y, weights=w)
    cov = np.average((x - mx) * (y - my), weights=w)
    sx = np.sqrt(np.average((x - mx) ** 2, weights=w))
    sy = np.sqrt(np.average((y - my) ** 2, weights=w))
    if sx == 0 or sy == 0:
        return float('nan'), len(x)
    return float(cov / (sx * sy)), len(x)


def consecutive_pairs(panel, metric, pos=None, need_both=MIN_MINS):
    xs, ys, ws = [], [], []
    for code, byseason in panel.items():
        for k in range(len(SEASONS) - 1):
            s0, s1 = SEASONS[k], SEASONS[k + 1]
            a, b = byseason.get(s0), byseason.get(s1)
            if not a or not b:
                continue
            if a['mins'] < need_both or b['mins'] < need_both:
                continue
            if pos and a['pos'] != pos:
                continue
            if a[metric] is None or b[metric] is None:
                continue
            xs.append(a[metric]); ys.append(b[metric])
            ws.append(min(a['mins'], b['mins']))
    return xs, ys, ws


# ------------------------------------------------------ 1. stability
def study_stability(panel):
    print('=' * 78)
    print('1. YEAR-OVER-YEAR STABILITY  (how much last season tells you about next)')
    print('=' * 78)
    metrics = ['pts90', 'g90', 'a90', 'xg90', 'xa90', 'xgi90', 'bps90',
               'bonus90', 'dc90', 'cs90', 'xgc90', 'starts']
    print(f"{'metric':<10}{'all':>14}{'DEF':>12}{'MID':>12}{'FWD':>12}")
    results = {}
    for m in metrics:
        line = f'{m:<10}'
        r_all, n_all = wcorr(*consecutive_pairs(panel, m))
        line += f'{r_all:>9.2f} (n={n_all:>3})' if n_all else f"{'—':>14}"
        results[m] = r_all
        for pos in ('DEF', 'MID', 'FWD'):
            r, n = wcorr(*consecutive_pairs(panel, m, pos=pos))
            line += f'{r:>8.2f}(n={n:>2})' if n >= 8 else f"{'—':>12}"
        print(line)
    print('\n  Read: a metric near 1.00 repeats itself; near 0.00 is noise and')
    print('  should be shrunk almost entirely towards the positional average.')
    return results


# ------------------------------------------- 2. does xG beat goals?
def study_xg_vs_goals(panel):
    print('\n' + '=' * 78)
    print('2. PREDICTING NEXT SEASON\'S GOALS: is xG a better guide than goals?')
    print('=' * 78)
    for pos in ('MID', 'FWD', None):
        rows = []
        for code, byseason in panel.items():
            for k in range(len(SEASONS) - 1):
                a = byseason.get(SEASONS[k]); b = byseason.get(SEASONS[k + 1])
                if not a or not b or a['mins'] < MIN_MINS or b['mins'] < MIN_MINS:
                    continue
                if pos and a['pos'] != pos:
                    continue
                rows.append((a['g90'], a['xg90'], b['g90'], min(a['mins'], b['mins'])))
        if len(rows) < 12:
            continue
        g0 = [r[0] for r in rows]; x0 = [r[1] for r in rows]
        g1 = [r[2] for r in rows]; w = [r[3] for r in rows]
        rg, n = wcorr(g0, g1, w)
        rx, _ = wcorr(x0, g1, w)
        lbl = pos or 'all outfield'
        verdict = 'xG wins' if rx > rg else 'goals win'
        print(f'  {lbl:<13} n={n:>3}   goals/90 -> next goals/90: {rg:.3f}   '
              f'xG/90 -> next goals/90: {rx:.3f}   [{verdict}]')
    print('\n  Same test for assists:')
    for pos in ('MID', 'FWD', None):
        rows = []
        for code, byseason in panel.items():
            for k in range(len(SEASONS) - 1):
                a = byseason.get(SEASONS[k]); b = byseason.get(SEASONS[k + 1])
                if not a or not b or a['mins'] < MIN_MINS or b['mins'] < MIN_MINS:
                    continue
                if pos and a['pos'] != pos:
                    continue
                rows.append((a['a90'], a['xa90'], b['a90'], min(a['mins'], b['mins'])))
        if len(rows) < 12:
            continue
        ra, n = wcorr([r[0] for r in rows], [r[2] for r in rows], [r[3] for r in rows])
        rxa, _ = wcorr([r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows])
        lbl = pos or 'all outfield'
        verdict = 'xA wins' if rxa > ra else 'assists win'
        print(f'  {lbl:<13} n={n:>3}   assists/90 -> next: {ra:.3f}   '
              f'xA/90 -> next: {rxa:.3f}   [{verdict}]')


# ------------------------------------- 3. share vs absolute rate
def study_share(panel):
    """NOT RUNNABLE -- documented here so the limitation is explicit.

    Testing share x volume needs to know which club a player was at in each past
    season. FPL's `history_past` endpoint does not carry team affiliation, and
    nothing else in the API recovers it, so the test cannot be run on this data.

    Consequence for the model: absolute per-90 rates are used, with a team-
    strength adjustment applied only to players who changed club this summer.
    That is the conservative choice -- it is what v1 did -- and it stays until
    per-season team history is available from another source.
    """
    print('\n' + '=' * 78)
    print('3. SHARE vs ABSOLUTE RATE — CANNOT BE TESTED ON THIS DATA')
    print('=' * 78)
    print('  FPL\'s history_past gives per-season stats but not which club the')
    print('  player was at, and share x volume is only distinguishable from an')
    print('  absolute rate for players who moved. Without team history there is')
    print('  no test, so the model keeps absolute rates rather than adopting a')
    print('  design that has not been validated.')
    return None


def _unused_share_impl(panel):
    team_tot = defaultdict(lambda: defaultdict(float))
    for code, byseason in panel.items():
        for s, r in byseason.items():
            team_tot[(r['team'], s)]['xg'] += r['xg']
            team_tot[(r['team'], s)]['xa'] += r['xa']

    # Share is defined the way the model would use it: what fraction of his
    # team's per-match xG does the player generate while he is on the pitch.
    #   xg90 = share x team_xg_per_match     =>     share = xg90 / team_xg_match
    # The decisive test is predictive, not correlational: predict next season's
    # xg90 either straight from this season's xg90, or by carrying the share
    # across and multiplying by the NEW team's volume.
    def rows_for(filter_moved=None):
        out = []
        for code, byseason in panel.items():
            for k in range(len(SEASONS) - 1):
                a = byseason.get(SEASONS[k]); b = byseason.get(SEASONS[k + 1])
                if not a or not b or a['mins'] < MIN_MINS or b['mins'] < MIN_MINS:
                    continue
                ta = team_tot[(a['team'], a['season'])]['xg'] / 38.0
                tb = team_tot[(b['team'], b['season'])]['xg'] / 38.0
                if ta <= 0.05 or tb <= 0.05:
                    continue
                moved = a['team'] != b['team']
                if filter_moved is not None and moved != filter_moved:
                    continue
                out.append(dict(x0=a['xg90'], x1=b['xg90'], ta=ta, tb=tb,
                                w=min(a['mins'], b['mins'])))
        return out

    for lbl, moved in (('all players', None), ('stayed at same club', False),
                       ('changed club', True)):
        rows = rows_for(moved)
        if len(rows) < 10:
            print(f'  {lbl:<22} too few observations (n={len(rows)})')
            continue
        w = np.array([r['w'] for r in rows], float)
        x1 = np.array([r['x1'] for r in rows])
        pred_abs = np.array([r['x0'] for r in rows])
        pred_shr = np.array([(r['x0'] / r['ta']) * r['tb'] for r in rows])
        mae_a = float(np.average(np.abs(pred_abs - x1), weights=w))
        mae_s = float(np.average(np.abs(pred_shr - x1), weights=w))
        win = 'share x volume' if mae_s < mae_a else 'absolute rate'
        print(f'  {lbl:<22} n={len(rows):>3}   MAE absolute {mae_a:.4f}   '
              f'MAE share x volume {mae_s:.4f}   [{win} wins]')
    print('\n  The "changed club" row is the one that matters: it is the only case')
    print('  where the two methods actually disagree.')


# ----------------------------------------------------- 4. aging
def study_aging(panel):
    print('\n' + '=' * 78)
    print('4. AGING  (per-90 points by age, minutes-weighted)')
    print('=' * 78)
    buckets = defaultdict(lambda: [0.0, 0.0])
    for code, byseason in panel.items():
        for s, r in byseason.items():
            if r['mins'] < MIN_MINS or not r['dob']:
                continue
            yr = int(s[:4])
            age = yr - int(r['dob'][:4])
            b = min(34, max(19, age))
            buckets[b][0] += r['pts90'] * r['mins']
            buckets[b][1] += r['mins']
    print(f"{'age':<6}{'pts/90':>9}{'player-seasons':>17}")
    ages = sorted(buckets)
    curve = {}
    for a in ages:
        tot, mins = buckets[a]
        if mins < 3000:
            continue
        curve[a] = tot / mins
        bar = '#' * int((tot / mins) * 9)
        print(f'{a:<6}{tot/mins:>9.2f}   {bar}')
    if curve:
        peak = max(curve, key=curve.get)
        print(f'\n  Peak per-90 output at age {peak}. '
              f'Effect size across the range is '
              f'{(max(curve.values()) / min(curve.values()) - 1) * 100:.0f}%.')
    return curve


if __name__ == '__main__':
    panel = load_panel()
    n_players = sum(1 for c in panel if len(panel[c]) >= 2)
    print(f'panel: {len(panel)} players, {n_players} with 2+ seasons, '
          f'seasons {SEASONS[0]}-{SEASONS[-1]}\n')
    study_stability(panel)
    study_xg_vs_goals(panel)
    study_share(panel)
    study_aging(panel)
