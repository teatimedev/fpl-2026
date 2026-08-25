"""
The DefCon dispersion test the README promised (P9).

player_model.defcon_hit_prob() treats per-match defensive-contribution counts
as negative binomial around a player's shrunk mean, with dispersion
r = 4 + 11 * evidence — a judgement made before any per-match data existed.
With gw_stat (P1) holding a few weeks of rows this compares Poisson against
negative-binomial fits of each player's per-match counts, estimates r from
the measured over-dispersion, and prints what the `r` line should be.

Also prints the per-match xG dispersion of regular attackers, which is the
XGI_MATCH_SD placeholder in retro.py.

    python v2/defcon_dispersion.py [--season 2026/27] [--min-matches 6]
"""
import argparse
import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
DB = HERE / 'fpl.db'
PROJ = HERE / 'projections_v2.json'
THRESHOLD = {'DEF': 10, 'MID': 12, 'FWD': 12}


def nb_loglik(counts, mean, r):
    """Negative-binomial log-likelihood of integer counts with the given
    mean and dispersion r (r -> inf is Poisson)."""
    p = r / (r + mean)
    ll = 0.0
    for x in counts:
        ll += (math.lgamma(x + r) - math.lgamma(r) - math.lgamma(x + 1)
               + r * math.log(p) + x * math.log(1 - p) if mean > 0 else (0.0 if x == 0 else -1e9))
    return ll


def poisson_loglik(counts, mean):
    if mean <= 0:
        return 0.0 if all(x == 0 for x in counts) else -1e9
    return sum(x * math.log(mean) - mean - math.lgamma(x + 1) for x in counts)


def load_counts(season, min_minutes=60):
    cx = sqlite3.connect(DB)
    if not cx.execute("SELECT name FROM sqlite_master WHERE name='gw_stat'").fetchone():
        raise SystemExit('gw_stat is empty; run fetch.py (or import_gw_history.py)')
    rows = cx.execute(
        'SELECT code, pos, minutes, defcon, xg, xa FROM gw_stat WHERE season = ? '
        'AND defcon IS NOT NULL AND minutes >= ?', (season, min_minutes)).fetchall()
    code_of = {r[0]: r[1] for r in cx.execute('SELECT id, code FROM player')}   # id -> code
    cx.close()
    by_player = defaultdict(list)
    for code, pos, mins, dc, xg, xa in rows:
        by_player[(code, pos)].append((mins, dc, xg or 0.0, xa or 0.0))
    return by_player, code_of


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', default='2026/27')
    ap.add_argument('--min-matches', type=int, default=6)
    args = ap.parse_args()
    by_player, code_of = load_counts(args.season)
    evidence = {}
    if PROJ.exists():
        proj = json.loads(PROJ.read_text())
        for p in proj['players']:
            code = code_of.get(p['id'])
            if code is not None:
                evidence[code] = float(p.get('dc_evidence', p.get('evidence', 0.5)) or 0.5)
    rounds = max((len(v) for v in by_player.values()), default=0)
    if rounds < args.min_matches:
        print(f'{args.season}: at most {rounds} full-match rows per player; this test '
              f'needs >= {args.min_matches} (about GW{args.min_matches + 1}). Nothing to do yet.')
        return

    print(f'{args.season}: per-match DefCon counts, players with >= {args.min_matches} '
          f'full matches (60+ minutes)\n')
    print(f"{'pos':<5}{'players':>8}{'mean':>7}{'var':>7}{'var/mean':>10}{'NB r (MoM)':>12}"
          f"{'dLL NB-Pois':>13}{'r = 4+11e':>11}")
    pooled = defaultdict(list)
    per_player = []
    for (code, pos), obs in by_player.items():
        if pos not in THRESHOLD or len(obs) < args.min_matches:
            continue
        counts = [dc for _, dc, _, _ in obs]
        mean = float(np.mean(counts))
        var = float(np.var(counts, ddof=1))
        if mean <= 0:
            continue
        r_mom = mean ** 2 / (var - mean) if var > mean else math.inf
        # MLE of r on a grid, per player
        best_r, best_ll = None, -math.inf
        for r in (1, 2, 3, 4, 6, 8, 10, 12, 15, 20, 30, 50, 100):
            ll = nb_loglik(counts, mean, r)
            if ll > best_ll:
                best_r, best_ll = r, ll
        dll = best_ll - poisson_loglik(counts, mean)
        e = evidence.get(code, 0.5)
        per_player.append(dict(code=code, pos=pos, n=len(counts), mean=mean, var=var,
                               r_mom=r_mom, r_mle=best_r, dll=dll, evidence=e,
                               r_model=4 + 11 * e))
        pooled[pos].append(per_player[-1])
    for pos in ('DEF', 'MID', 'FWD'):
        rows = pooled.get(pos, [])
        if not rows:
            continue
        mean = np.mean([r['mean'] for r in rows])
        var = np.mean([r['var'] for r in rows])
        r_finite = [r['r_mom'] for r in rows if math.isfinite(r['r_mom'])]
        r_med = float(np.median(r_finite)) if r_finite else math.inf
        dll = float(np.mean([r['dll'] for r in rows]))
        r_model = float(np.median([r['r_model'] for r in rows]))
        print(f'{pos:<5}{len(rows):>8}{mean:>7.2f}{var:>7.2f}{var / mean:>10.2f}'
              f'{r_med:>12.1f}{dll:>13.2f}{r_model:>11.1f}')
    print('\nRead: var/mean > 1 means over-dispersed (negative binomial earns its keep);'
          '\n      NB r (MoM) is the dispersion the data implies — compare with the '
          'r = 4 + 11*evidence line;\n      dLL > 0 means NB fits better than Poisson '
          'per player on average.')
    # does r rise with evidence, as the model assumes?
    bands = defaultdict(list)
    for r in per_player:
        if math.isfinite(r['r_mom']):
            bands['<0.7' if r['evidence'] < 0.7 else ('0.7-0.9' if r['evidence'] < 0.9 else '>=0.9')].append(r['r_mom'])
    if bands:
        print('\nmeasured r by evidence band (median):')
        for band in ('<0.7', '0.7-0.9', '>=0.9'):
            if bands.get(band):
                print(f'  evidence {band:<8} n={len(bands[band]):>3}  r {np.median(bands[band]):.1f}')
        print('  (the model line gives 4-11.7 / 11.7-13.9 / 13.9-15 for those bands)')

    # per-match xG dispersion for regular attackers -> retro.XGI_MATCH_SD
    sds = []
    for (code, pos), obs in by_player.items():
        if pos not in ('MID', 'FWD') or len(obs) < args.min_matches:
            continue
        xgs = [xg for _, _, xg, _ in obs]
        if np.mean(xgs) >= 0.25:
            sds.append(float(np.std(xgs, ddof=1)))
    if sds:
        print(f'\nper-match xG sd for attackers averaging >= 0.25 xG (n={len(sds)}): '
              f'median {np.median(sds):.2f}, mean {np.mean(sds):.2f}  '
              f'-> retro.XGI_MATCH_SD (placeholder 0.40)')


if __name__ == '__main__':
    main()
