"""How wide is the minutes residual around a SHRUNK prediction?

The first pass measured the spread of next-season starts around "however many
he started last season" -- the spread around a naive predictor. v2 does not
use a naive predictor: minutes_model() shrinks the observed start rate towards
a club/position pecking-order prior, and that shrinkage was already shown to be
well calibrated on the mean (0.708 predicted vs 0.705 realised).

A shrunk predictor has a smaller residual than a naive one by construction, so
using the naive spread inflates the simulation. Fit the same shrinkage on the
history and measure the residual around THAT instead.
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
import sqlite3, numpy as np

cx = sqlite3.connect('v2/fpl.db')
rows = cx.execute('SELECT code, season, starts, minutes FROM season_stat').fetchall()
cx.close()
by = {}
for c, s, st, m in rows:
    by.setdefault(c, {})[s] = (st, m)
S = ['2022/23', '2023/24', '2024/25', '2025/26']

x, y = [], []
for c, d in by.items():
    for p, n in zip(S, S[1:]):
        if p in d and n in d and d[p][1] >= 450:
            x.append(d[p][0] / 38.0); y.append(d[n][0] / 38.0)
x, y = np.array(x), np.array(y)
b = np.polyfit(x, y, 1)
pred = np.polyval(b, x)
res = y - pred
print(f'n = {len(x)} pairs (450+ minutes in the prior season)')
print(f'shrinkage fit: next = {b[0]:.2f} x prior + {b[1]:.2f}   '
      f'(v2 shrinks towards a pecking-order prior, same shape)')
print(f'residual around the shrunk prediction: sd {res.std(ddof=2):.3f} of a season '
      f'(= {res.std(ddof=2)*38:.1f} starts)')
print(f'residual around the NAIVE prediction:  sd {(y-x).std(ddof=1):.3f} '
      f'(= {(y-x).std(ddof=1)*38:.1f} starts)  <- what the first pass used')
print(f'\nresidual is left-skewed: skew {float(((res-res.mean())**3).mean()/res.std()**3):+.2f}')
for q in (5, 10, 25, 50, 75, 90, 95):
    print(f'   {q:>3}th pct of residual: {np.percentile(res, q):+.3f}')
np.save(OUT / 'min_resid.npy', res)

# and for the top end specifically, where the race is decided
m = x >= 0.7
print(f'\nfor players who already started 70%+ of games (n={m.sum()}):')
print(f'   predicted {pred[m].mean():.2f}, realised {y[m].mean():.2f}, '
      f'residual sd {res[m].std(ddof=1):.3f}')
np.save(OUT / 'min_resid_top.npy', res[m])
