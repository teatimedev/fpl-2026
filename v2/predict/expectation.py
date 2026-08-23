"""Overachiever and underachiever need a baseline to beat.

There are no 2026/27 bookmaker season odds in the database (the `market` table
is empty until football-data posts forward prices about a week out), so
expectation has to be built from what IS here. Two independent proxies:

  1. LAST SEASON'S TABLE. What everyone anchors on.
  2. FPL'S OWN PRICING. FPL sets prices from expected points and expected
     ownership -- it is a published, money-backed forecast of how good each
     squad is, and it already prices in the summer's transfers, which the
     table cannot. Squad value here is the sum of a club's 16 dearest players,
     which is close to a first-team wage/quality proxy.

The two disagree exactly where the interesting answers are.
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
import json, sqlite3, numpy as np

cx = sqlite3.connect('v2/fpl.db')
rows = cx.execute('SELECT season, home, away, hg, ag FROM match '
                  "WHERE season='2025/26'").fetchall()
tab = {}
for _, h, a, hg, ag in rows:
    for t in (h, a): tab.setdefault(t, [0, 0, 0])
    tab[h][1] += hg; tab[h][2] += ag
    tab[a][1] += ag; tab[a][2] += hg
    if hg > ag: tab[h][0] += 3
    elif ag > hg: tab[a][0] += 3
    else: tab[h][0] += 1; tab[a][0] += 1
prices = {}
for short, price in cx.execute(
        'SELECT t.short, p.price FROM player p JOIN team t ON p.team_id = t.id'):
    prices.setdefault(short, []).append(price)
cx.close()

sv = json.load(open('v2/season_view.json'))
TEAMS = sorted(sv['atk'])
val = {t: round(sum(sorted(prices.get(t, []), reverse=True)[:16]), 1) for t in TEAMS}

order = sorted(tab, key=lambda t: (-tab[t][0], -(tab[t][1] - tab[t][2])))
last_pos = {t: i + 1 for i, t in enumerate(order)}
print('2025/26 final table')
for t in order:
    p, gf, ga = tab[t]
    mark = '' if t in TEAMS else '   (relegated)'
    print(f'{last_pos[t]:>3}  {t:<5}{p:>4} pts  {gf:>3}:{ga:<3}{mark}')

print('\nFPL squad value 2026/27 — top 16 players by price')
for t in sorted(val, key=lambda t: -val[t]):
    prev = last_pos.get(t)
    print(f'  {t:<5}£{val[t]:>6.1f}m   ' +
          (f'finished {prev} last season' if prev else 'promoted'))

# expectation = rank on squad value, blended with last season's finish where
# there is one. Promoted clubs get 18th/19th/20th by squad value order.
vrank = {t: i + 1 for i, t in enumerate(sorted(val, key=lambda t: -val[t]))}
exp = {}
for t in TEAMS:
    if t in last_pos:
        # last season's finish among the 17 survivors, re-ranked 1..17
        surv = [x for x in order if x in TEAMS]
        lp = surv.index(t) + 1
        exp[t] = 0.5 * lp + 0.5 * vrank[t]
    else:
        exp[t] = 0.5 * 18.5 + 0.5 * vrank[t]
er = {t: i + 1 for i, t in enumerate(sorted(exp, key=lambda t: exp[t]))}
json.dump({'expected_rank': er, 'value': val,
           'last_pos': {t: last_pos.get(t) for t in TEAMS},
           'last_pts': {t: tab.get(t, [None])[0] for t in TEAMS}},
          open(OUT / 'expectation.json', 'w'), indent=1)
print('\nconsensus expected finish (half last season, half FPL squad value)')
for t in sorted(er, key=lambda t: er[t]):
    print(f'  {er[t]:>3}  {t}')
