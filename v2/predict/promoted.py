"""How wide is the promoted-club prior really?

season_view gives Coventry and Hull a single point estimate (the fitted
promoted prior). Twelve promoted club-seasons sit in the database; measure how
far each actually landed from that prior, so the sim can carry the real spread
rather than treating every promoted side as identical.
"""
from pathlib import Path as _P
import os as _os, sys as _sys
_HERE = _P(__file__).resolve().parent
OUT = _HERE / '_out'
OUT.mkdir(exist_ok=True)
ROOTDIR = _HERE.parents[1]
_os.chdir(ROOTDIR)
_sys.path.insert(0, str(_HERE))
_sys.path.insert(0, str(_HERE.parent))
import sys, json, numpy as np
import teams_model as TM

matches = TM.load_matches()
seasons = sorted({m['season'] for m in matches})
fits = {s: TM.fit([m for m in matches if m['season'] == s], half_life_days=1e6)
        for s in seasons}

prior = json.load(open('v2/team_ratings.json'))['promoted_prior']
print(f"prior offset from mean: atk {prior['atk']:+.3f}  dfn {prior['dfn']:+.3f}\n")

ra, rd = [], []
print(f"{'season':<10}{'club':<6}{'atk-mean':>10}{'dfn-mean':>10}")
for prev, s in zip(seasons, seasons[1:]):
    up = set(fits[s]['teams']) - set(fits[prev]['teams'])
    ma = np.mean(list(fits[s]['atk'].values()))
    md = np.mean(list(fits[s]['dfn'].values()))
    for t in sorted(up):
        a = fits[s]['atk'][t] - ma
        d = fits[s]['dfn'][t] - md
        ra.append(a); rd.append(d)
        print(f'{s:<10}{t:<6}{a:>+10.3f}{d:>+10.3f}')

ra, rd = np.array(ra), np.array(rd)
print(f'\nn = {len(ra)}')
print(f'promoted clubs, actual offset from league mean: '
      f'atk {ra.mean():+.3f} (sd {ra.std(ddof=1):.3f})   '
      f'dfn {rd.mean():+.3f} (sd {rd.std(ddof=1):.3f})')
# strip single-season estimation noise (0.134 / 0.145 measured earlier)
print(f'less estimation noise -> true spread  '
      f'atk {np.sqrt(max(ra.var(ddof=1)-0.134**2,1e-6)):.3f}   '
      f'dfn {np.sqrt(max(rd.var(ddof=1)-0.145**2,1e-6)):.3f}')
