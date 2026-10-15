"""Item 4: does the per-position calibration k belong on the whole projection?

Forward check on this season's ARCHIVED deadline forecasts (data/history/
gw{n}.json, written before each deadline) against the realised points in
gw{n}_actual.json. Each snapshot row's projection is rebuilt with
retro.expected_components() at k = 1 and split into

  rate   attack (xG/xA), bonus, DefCon, saves — shrunk per-90 estimates
  fixed  appearance, clean sheets, goals conceded, cards — FPL's scoring
         rules applied to minutes and the team model

and scored three ways: k on everything (production), k on the rate part
only (same stored k), and no k. The retro decomposition of GW1-4 shows why
this matters: its `other` bucket (appearance/saves/cards at the minutes
actually played, times k) runs at -0.33 points per likely starter per
gameweek for MID and FWD, i.e. k is inflating rule-fixed appearance points.

    .venv/bin/python research/calibration_scope_20260923.py
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'v2'))
import retro as RT  # noqa: E402

HISTORY = ROOT / 'data' / 'history'
RATE = ('attack', 'bonus', 'defcon', 'saves')
FIXED = ('cs', 'gc', 'appearance', 'yellow')


def main(min_start=0.5):
    obs = defaultdict(list)
    for n in range(1, 39):
        snap_path, act_path = HISTORY / f'gw{n}.json', HISTORY / f'gw{n}_actual.json'
        if not (snap_path.exists() and act_path.exists()):
            continue
        snap, act = json.loads(snap_path.read_text()), json.loads(act_path.read_text())
        points = act.get('points') or {}
        team_cs = snap.get('team_cs') or {}
        for row in snap['players']:
            if row.get('xg90') is None or str(row['id']) not in points:
                continue
            p_start, p_cameo, sm, cm = RT.deadline_mixture(row)
            if p_start < min_start:
                continue
            c = RT.expected_components(row, team_cs.get(row['team']) or [], p_start,
                                       p_cameo, sm, cm, 1.0)
            k = float(row.get('k') or 1.0)
            rate = sum(c[x] for x in RATE)
            fixed = sum(c[x] for x in FIXED)
            obs[row['pos']].append(dict(gw=n, k=k, rate=rate, fixed=fixed,
                                        proj=float(row.get('proj') or 0.0),
                                        actual=float(points[str(row['id'])][0])))
    print(f"likely starters (deadline p_start >= {min_start}), archived GWs "
          f"{sorted({o['gw'] for v in obs.values() for o in v})}")
    print(f"{'pos':<5}{'n':>5}{'k':>7}{'variant':<14}{'mean proj':>10}{'mean act':>9}"
          f"{'act/proj':>9}{'MAE':>7}{'RMSE':>7}")
    tot = defaultdict(list)
    for pos in ('GKP', 'DEF', 'MID', 'FWD'):
        rows = obs.get(pos, [])
        if not rows:
            continue
        a = np.array([o['actual'] for o in rows])
        variants = {
            'k on all': np.array([o['k'] * (o['rate'] + o['fixed']) for o in rows]),
            'k on rates': np.array([o['fixed'] + o['k'] * o['rate'] for o in rows]),
            'no k': np.array([o['rate'] + o['fixed'] for o in rows]),
        }
        k = np.mean([o['k'] for o in rows])
        for name, p in variants.items():
            tot[name].append((p, a))
            print(f'{pos:<5}{len(rows):>5}{k:>7.3f} {name:<13}{p.mean():>10.3f}{a.mean():>9.3f}'
                  f'{a.sum() / p.sum():>9.3f}{np.abs(p - a).mean():>7.3f}'
                  f'{np.sqrt(((p - a) ** 2).mean()):>7.3f}')
    print()
    for name, parts in tot.items():
        p = np.concatenate([x[0] for x in parts])
        a = np.concatenate([x[1] for x in parts])
        print(f'ALL  {len(p):>5}        {name:<13}{p.mean():>10.3f}{a.mean():>9.3f}'
              f'{a.sum() / p.sum():>9.3f}{np.abs(p - a).mean():>7.3f}'
              f'{np.sqrt(((p - a) ** 2).mean()):>7.3f}')
    # paired squared-error difference against production, with its standard
    # error (player-gameweeks treated as independent, which flatters it)
    print('\nsquared error vs "k on all" (negative = better), mean +- 1 s.e.:')
    for name in ('k on rates', 'no k'):
        for i, pos in enumerate(p for p in ('GKP', 'DEF', 'MID', 'FWD') if obs.get(p)):
            (pb, a), (pv, _) = tot['k on all'][i], tot[name][i]
            d = (pv - a) ** 2 - (pb - a) ** 2
            print(f'  {name:<11}{pos:<5}{d.mean():>+8.3f} +- {d.std(ddof=1) / np.sqrt(len(d)):.3f}')


if __name__ == '__main__':
    main()
