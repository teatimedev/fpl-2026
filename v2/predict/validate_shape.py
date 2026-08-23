"""Does the simulated season LOOK like a real one?

A projection table is naturally compressed -- every club sits near its mean.
A realised table is not. The check that matters is whether the simulated
REALISED tables have the same dispersion as the four real ones in the database:
champion's points, the fourth-place line, the relegation line, and the spread.
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
import sys, sqlite3, numpy as np, json
import season_sim as S

cx = sqlite3.connect('v2/fpl.db')
rows = cx.execute('SELECT season, home, away, hg, ag FROM match WHERE hg IS NOT NULL').fetchall()
cx.close()
real = {}
for s, h, a, hg, ag in rows:
    d = real.setdefault(s, {})
    for t in (h, a): d.setdefault(t, [0, 0, 0])
    d[h][1] += hg; d[h][2] += ag
    d[a][1] += ag; d[a][2] += hg
    if hg > ag: d[h][0] += 3
    elif ag > hg: d[a][0] += 3
    else: d[h][0] += 1; d[a][0] += 1

print(f"{'season':<10}{'1st':>6}{'2nd':>6}{'4th':>6}{'7th':>6}{'17th':>6}{'18th':>6}{'20th':>6}{'sd':>7}")
reals = []
for s in sorted(real):
    p = sorted((v[0] for v in real[s].values()), reverse=True)
    reals.append(p)
    print(f"{s:<10}{p[0]:>6}{p[1]:>6}{p[3]:>6}{p[6]:>6}{p[16]:>6}{p[17]:>6}{p[19]:>6}"
          f"{np.std(p):>7.1f}")
R = np.array(reals)
print(f"{'REAL mean':<10}" + ''.join(f'{R[:,i].mean():>6.1f}' for i in (0,1,3,6,16,17,19))
      + f"{np.mean([np.std(p) for p in reals]):>7.1f}")

for noise in (True, False):
    pts, gf, ga, cs, _ = S.run(20000, param_noise=noise)
    sp = -np.sort(-pts, axis=1)
    lab = 'SIM +noise' if noise else 'SIM raw'
    print(f"{lab:<10}" + ''.join(f'{sp[:,i].mean():>6.1f}' for i in (0,1,3,6,16,17,19))
          + f"{pts.std(axis=1).mean():>7.1f}")
    if noise:
        print('\n  champion points   5th/50th/95th pct: '
              f'{np.percentile(sp[:,0],5):.0f} / {np.percentile(sp[:,0],50):.0f} / {np.percentile(sp[:,0],95):.0f}')
        print(f'  4th place line    5th/50th/95th pct: '
              f'{np.percentile(sp[:,3],5):.0f} / {np.percentile(sp[:,3],50):.0f} / {np.percentile(sp[:,3],95):.0f}')
        print(f'  17th (survival)   5th/50th/95th pct: '
              f'{np.percentile(sp[:,16],5):.0f} / {np.percentile(sp[:,16],50):.0f} / {np.percentile(sp[:,16],95):.0f}')
