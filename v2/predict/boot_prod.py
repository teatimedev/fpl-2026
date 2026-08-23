"""Bootstrap the PRODUCTION fit (4 seasons, 280-day half-life) to get the
estimation uncertainty that sits on the ratings the season view actually uses."""
from pathlib import Path as _P
import os as _os, sys as _sys
_HERE = _P(__file__).resolve().parent
OUT = _HERE / '_out'
OUT.mkdir(exist_ok=True)
ROOTDIR = _HERE.parents[1]
_os.chdir(ROOTDIR)
_sys.path.insert(0, str(_HERE))
_sys.path.insert(0, str(_HERE.parent))
import sys, time, numpy as np
import teams_model as TM

matches = TM.load_matches()
HL = 280
t0 = time.time()
base = TM.fit(matches, half_life_days=HL)
print(f'base fit {time.time()-t0:.1f}s')

rng = np.random.default_rng(11)
BA, BD = [], []
for i in range(20):
    idx = rng.integers(0, len(matches), len(matches))
    r = TM.fit([matches[j] for j in idx], half_life_days=HL)
    BA.append(r['atk']); BD.append(r['dfn'])
    print('.', end='', flush=True)
print()

# only clubs in 2026/27 with a real record
import json
sv = json.load(open('v2/season_view.json'))
ts = [t for t in sv['atk'] if t in base['atk']]
sa = {t: float(np.std([b[t] for b in BA if t in b], ddof=1)) for t in ts}
sd = {t: float(np.std([b[t] for b in BD if t in b], ddof=1)) for t in ts}
print(f'\nproduction-fit estimation sd: atk {np.mean(list(sa.values())):.3f}  '
      f'dfn {np.mean(list(sd.values())):.3f}')
for t in sorted(ts):
    print(f'  {t}  atk±{sa[t]:.3f}  dfn±{sd[t]:.3f}')
json.dump({'atk_se': sa, 'dfn_se': sd}, open(OUT / 'rating_se.json','w'), indent=1)
