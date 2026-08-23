"""How much do fitted team ratings actually move year to year?

Needed because a pure Poisson season sim treats the fitted ratings as truth.
They are not: ten new managers, a transfer window, and 38 games of estimation
noise all sit on top. Measure the real drift so the sim can carry it.
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
import sys, sqlite3, numpy as np
from pathlib import Path
import teams_model as TM
from datetime import datetime

matches = TM.load_matches()
seasons = sorted({m['season'] for m in matches})
print('seasons:', seasons)

fits = {}
for s in seasons:
    ms = [m for m in matches if m['season'] == s]
    # huge half-life => no within-season decay, a clean single-season fit
    r = TM.fit(ms, half_life_days=1e6)
    fits[s] = r
    print(f"{s}: {len(r['teams'])} teams, home_adv {r['home_adv']:+.3f}, rho {r['rho']:+.3f}")

da, dd = [], []
for a, b in zip(seasons, seasons[1:]):
    both = set(fits[a]['atk']) & set(fits[b]['atk'])
    for t in both:
        da.append(fits[b]['atk'][t] - fits[a]['atk'][t])
        dd.append(fits[b]['dfn'][t] - fits[a]['dfn'][t])
da, dd = np.array(da), np.array(dd)
print(f'\nn pairs = {len(da)}')
print(f'observed YoY change  atk sd {da.std(ddof=1):.3f}   dfn sd {dd.std(ddof=1):.3f}')

# estimation noise: a rating from one season is itself uncertain. Bootstrap the
# 2025/26 fit by resampling matches, to see how much of the drift is just noise.
ms = [m for m in matches if m['season'] == seasons[-1]]
rng = np.random.default_rng(7)
boot_a, boot_d = [], []
for _ in range(25):
    idx = rng.integers(0, len(ms), len(ms))
    r = TM.fit([ms[i] for i in idx], half_life_days=1e6)
    boot_a.append(r['atk'])
    boot_d.append(r['dfn'])
ts = sorted(set.intersection(*[set(b) for b in boot_a]))
sa = np.mean([np.std([b[t] for b in boot_a], ddof=1) for t in ts])
sd_ = np.mean([np.std([b[t] for b in boot_d], ddof=1) for t in ts])
print(f'estimation noise (bootstrap) atk sd {sa:.3f}   dfn sd {sd_:.3f}')

true_a = np.sqrt(max(da.var(ddof=1) - 2 * sa**2, 1e-6))
true_d = np.sqrt(max(dd.var(ddof=1) - 2 * sd_**2, 1e-6))
print(f'=> genuine year-to-year drift  atk sd {true_a:.3f}   dfn sd {true_d:.3f}')
