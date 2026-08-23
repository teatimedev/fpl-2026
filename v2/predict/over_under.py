"""Overachiever and underachiever, for a player.

For a team the baseline was last season plus squad value. For a player FPL
supplies a sharper one: the price. FPL sets it from expected points and
expected ownership, it is published, and every manager in the game is trading
against it.

Fitting points against price directly does not work. Linear extrapolation
prices Haaland's £15.5m at 205 points and a log-log fit -- dragged steep by the
several hundred £4.5m players who will never start -- makes it 264, so both
report the two best players in the game as underachievers. That is an artefact
of the functional form, not a finding. Ranks avoid the problem entirely: within
each position, where does the price say he should finish, and where does the
model say he will? Ownership is the second axis, because a player nobody owns
cannot disappoint anyone.
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
import json, numpy as np

P = json.load(open(OUT / 'players.json'))
for pos in ('GKP', 'DEF', 'MID', 'FWD'):
    g = [r for r in P if r['pos'] == pos]
    # price rank, ties broken by ownership -- two £6.0m midfielders are not
    # equally backed, and the crowd's split is itself part of the expectation
    for i, r in enumerate(sorted(g, key=lambda r: (-r['price'], -r['sel'])), 1):
        r['prank'] = i
    for i, r in enumerate(sorted(g, key=lambda r: -r['mean']), 1):
        r['mrank'] = i
    for r in g:
        r['move'] = r['prank'] - r['mrank']       # + = model rates him higher
        r['ppm'] = r['mean'] / r['price']
        r['npos'] = len(g)

print('=== PLAYER OVERACHIEVER — cheap, and the model has him far up the order ===')
print(f"{'':<3}{'player':<15}{'tm':<5}{'pos':<4}{'£':>5}{'own%':>6}{'proj':>6}"
      f"{'pts/£m':>8}{'price rk':>9}{'model rk':>9}{'move':>6}{'top10':>7}")
cand = [r for r in P if r['price'] <= 6.5 and r['mean'] > 100 and r['move'] > 0]
for k, r in enumerate(sorted(cand, key=lambda r: -r['move'])[:12], 1):
    print(f"{k:<3}{r['name']:<15}{r['team']:<5}{r['pos']:<4}{r['price']:>5.1f}"
          f"{r['sel']:>6.1f}{r['mean']:>6.0f}{r['ppm']:>8.1f}"
          f"{r['prank']:>9}{r['mrank']:>9}{r['move']:>+6}{r['top10']*100:>6.1f}%")

print('\n=== PLAYER UNDERACHIEVER — expensive, well owned, and rated below both ===')
print(f"{'':<3}{'player':<15}{'tm':<5}{'pos':<4}{'£':>5}{'own%':>6}{'proj':>6}"
      f"{'pts/£m':>8}{'price rk':>9}{'model rk':>9}{'move':>6}")
cand = [r for r in P if r['price'] >= 6.5 and r['sel'] >= 4.0]
for k, r in enumerate(sorted(cand, key=lambda r: r['move'])[:12], 1):
    print(f"{k:<3}{r['name']:<15}{r['team']:<5}{r['pos']:<4}{r['price']:>5.1f}"
          f"{r['sel']:>6.1f}{r['mean']:>6.0f}{r['ppm']:>8.1f}"
          f"{r['prank']:>9}{r['mrank']:>9}{r['move']:>+6}")

print('\n  the template, and what the model makes of it')
print(f"    {'player':<15}{'tm':<5}{'£':>6}{'own%':>7}{'proj':>6}"
      f"{'price rk':>9}{'model rk':>9}   verdict")
for r in sorted(P, key=lambda r: -r['sel'])[:14]:
    v = ('backs it' if r['move'] > 3 else
         'fades it' if r['move'] < -3 else 'fair')
    print(f"    {r['name']:<15}{r['team']:<5}{r['price']:>6.1f}{r['sel']:>7.1f}"
          f"{r['mean']:>6.0f}{r['prank']:>9}{r['mrank']:>9}   {v}")
json.dump(P, open(OUT / 'players.json', 'w'))
