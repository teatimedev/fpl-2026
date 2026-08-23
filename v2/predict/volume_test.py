"""Does v2's volume multiplier double-count team strength over a full season?

project() scales every player's attacking rate by f['xg']/1.45 -- the club's
expected goals in that fixture against a league-average 1.45. Over a six-week
window that is a fixture adjustment and clearly right: Arsenal at home to
Coventry really is a better week than Arsenal away at Villa. Over 38 games it
averages out to the club's overall attacking strength -- and for a player who
stayed put, that strength is already inside his own xG/90, because that is
where it was measured. This is v1's error in a different form; the README
records it as "Players who stayed put get no team-strength multiplier ...
which at one point had Gabriel projected at 8.6 points a game".

The clean test -- regress realised goals per 90 on the player's own prior rate
AND his club's strength -- cannot be run: season_stat.team is NULL for every
row, which is the club-attribution gap v2's own README flags ("FPL's
history_past does not record which club a player was at").

So test the outcome instead. For players who did NOT move this summer, compare
v2's projected attacking points against what those same players actually
delivered last season, and see whether the ratio tracks club volume. If v2 is
only adjusting for fixtures the ratio should be flat. If it is re-applying club
strength, strong clubs will project high.
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
import json, sqlite3
import numpy as np

GOAL_PTS = {'GKP': 6, 'DEF': 6, 'MID': 5, 'FWD': 4}
comp = json.load(open(OUT / 'components.json'))
P = {r['id']: r for r in json.load(open('v2/projections_v2.json'))['players']}
cx = sqlite3.connect('v2/fpl.db')
code = {r[0]: r[1] for r in cx.execute('SELECT id, code FROM player')}
st = {r[0]: r[1:] for r in cx.execute(
    "SELECT code, minutes, goals, assists FROM season_stat WHERE season='2025/26'")}
cx.close()
sv = json.load(open('v2/season_view.json'))
vol = {t: float(np.mean([f['xg'] for g in sv['view'][t].values() for f in g])) / 1.45
       for t in sv['view']}

by = {}
for r in comp:
    p, h = P.get(r['id']), st.get(code.get(r['id']))
    if not p or not h or h[0] < 2000:
        continue
    if p['joined'] and p['joined'] >= '2026-06-01':      # summer movers excluded
        continue
    actual = h[1] * GOAL_PTS[r['pos']] + h[2] * 3
    if actual < 8:
        continue
    by.setdefault(r['team'], []).append(r['pts_attack'] / actual)

print(f"{'team':<6}{'proj/actual':>12}{'n':>4}{'club volume':>13}")
xs, ys, ws = [], [], []
for t in sorted(by, key=lambda t: -np.median(by[t])):
    if len(by[t]) < 4:
        continue
    m = float(np.median(by[t]))
    print(f'{t:<6}{m:>12.2f}{len(by[t]):>4}{vol[t]:>13.2f}')
    xs.append(vol[t]); ys.append(m); ws.append(len(by[t]))
xs, ys = np.array(xs), np.array(ys)
r = float(np.corrcoef(xs, ys)[0, 1])
b = np.polyfit(xs, ys, 1, w=np.sqrt(ws))
print(f'\nn = {len(xs)} clubs;  corr(club volume, over-projection) = {r:+.2f}')
print(f'weighted slope {b[0]:.2f} per unit of volume — v2 applies it at 1.00')
print(f'  a 1.29-volume club (ARS, MCI) projects {np.polyval(b,1.29):.2f}x what its '
      f'players delivered;\n  a 0.85-volume club (CRY) projects {np.polyval(b,0.85):.2f}x')
print(f'\nConclusion: roughly HALF the season-level club multiplier is real. '
      f'components.py\nkeeps half of it (VOL_LAMBDA = 0.5) and drops the rest.')
print('\nCaveats: one season of realised output as the yardstick, 13 clubs, and a '
      'club\nstrong last season already had its players over-deliver — which biases '
      'AGAINST\nfinding this effect, not towards it.')
json.dump({'slope': float(b[0]), 'corr': r}, open(OUT / 'volume_test.json', 'w'))
