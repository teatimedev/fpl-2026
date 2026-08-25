"""Break every player's season projection into the parts that behave differently.

A season total is one number, but its pieces carry different risk. Goals and
assists are Poisson and swing wildly; appearance points track minutes and
nothing else; clean-sheet points belong to the team, not the player. To ask
"who finishes top" the pieces have to be separated and then varied on their own
terms. This re-runs v2's own projection loop and records each component instead
of only the sum.
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
import json, sys, io, contextlib
from pathlib import Path
import numpy as np
import player_model as PM

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    players = PM.load()
    PM.GAMES_PLAYED.update(PM.games_played())
    PM.TEAM_FIXTURES.update(PM.team_fixtures())
    PM.SNAPSHOT_STATUS.update(PM.load_snapshot_status())
    view = json.loads(PM.SEASON_VIEW.read_text())
    priors = PM.positional_priors(players)
    rows = PM.project(players, view, priors)
cal = [l for l in buf.getvalue().splitlines() if 'calibration' in l]
print('\n'.join(cal))
# the multipliers now travel on the rows (frozen in v2/calibration.json, P4)
# rather than only in calibrate()'s stdout
K = {r['pos']: r.get('calibration_k', 1.0) for r in rows}
print()

out = []
for p in players.values():
    pos = p['pos']
    start_rate, mps = PM.minutes_model(p, players)
    frac = mps / 90.0
    available = p['status'] != 'u'
    p_play = (start_rate + (1 - start_rate) * 0.20) if available else 0.0
    if not available:
        start_rate = 0.0
    xg90, w_xg = PM.shrink(p, 'xg90', priors)
    xa90, _ = PM.shrink(p, 'xa90', priors)
    dc90, w_dc = PM.shrink(p, 'dc90', priors)
    bonus90, _ = PM.shrink(p, 'bonus90', priors)
    saves90, _ = PM.shrink(p, 'saves90', priors)
    yellow90, _ = PM.shrink(p, 'yellow90', priors)
    af = PM.age_factor(p['dob'])
    ov = PM.OVERLAY.get(p['id'], {})
    rm = ov.get('rate_mult', 1.0)
    xg90 *= af * rm
    xa90 *= af * rm

    fx = view['view'].get(p['team'], {})
    c = dict(goals=0.0, assists=0.0, cs=0.0, defcon=0.0, appear=0.0,
             bonus=0.0, saves=0.0, yellow=0.0, gcded=0.0, team_xg=0.0)
    for gw in range(1, 39):
        for f in (fx.get(str(gw)) or []):
            vol = f['xg'] / 1.45
            c['team_xg'] += f['xg']
            c['goals'] += xg90 * frac * vol * p_play
            c['assists'] += xa90 * frac * vol * p_play
            if PM.CS_PTS[pos]:
                c['cs'] += PM.CS_PTS[pos] * f['cs'] * start_rate
            if pos in ('GKP', 'DEF'):
                c['gcded'] += PM.expected_floor_div(f['xgc'], 2) * start_rate
            if pos == 'GKP':
                c['saves'] += PM.expected_floor_div(saves90 * frac, 3) * start_rate
            thr = PM.DC_THRESHOLD[pos]
            if thr and dc90 > 0:
                c['defcon'] += 2.0 * PM.defcon_hit_prob(dc90 * frac, thr, w_dc) * p_play
            c['appear'] += start_rate * 2.0 + (p_play - start_rate) * 1.0
            c['bonus'] += bonus90 * frac * p_play * 0.85
            c['yellow'] += yellow90 * frac * p_play
    k = K.get(pos, 1.0)
    gp = PM.GOAL_PTS[pos]
    out.append(dict(
        id=p['id'], name=p['name'], team=p['team'], pos=pos, price=p['price'],
        sel=p['sel_pct'], status=p['status'], pens=p['pens'],
        start_rate=round(start_rate, 4), mps=round(mps, 2),
        p_play=round(p_play, 4), xg90=round(xg90, 5), xa90=round(xa90, 5),
        k=k,
        eg=round(c['goals'], 3),                # expected goals, pre-calibration
        ea=round(c['assists'], 3),
        pts_attack=round((c['goals'] * gp + c['assists'] * 3.0) * k, 2),
        pts_cs=round(c['cs'] * k, 2),
        pts_defcon=round(c['defcon'] * k, 2),
        pts_appear=round(c['appear'] * k, 2),
        pts_bonus=round(c['bonus'] * k, 2),
        pts_saves=round(c['saves'] * k, 2),
        pts_neg=round((c['yellow'] + c['gcded']) * k, 2),
    ))
for r in out:
    r['total'] = round(r['pts_attack'] + r['pts_cs'] + r['pts_defcon']
                       + r['pts_appear'] + r['pts_bonus'] + r['pts_saves']
                       - r['pts_neg'], 2)

# Half the season-level club volume multiplier is removed (volume_test.py: the
# measured coefficient is 0.56 against the 1.00 v2 applies). Fixture-to-fixture
# variation, which is what the six-week window trades on and what the model was
# validated for, is untouched -- only the club's season-average level moves.
VOL_LAMBDA = 0.5
vol = {t: float(np.mean([f['xg'] for g in view['view'][t].values() for f in g])) / 1.45
       for t in view['view']}
_w = sum(r['pts_attack'] for r in out)
_raw = sum(r['pts_attack'] * vol[r['team']] ** -(1 - VOL_LAMBDA) for r in out)
_norm = _w / _raw                       # keep the league-wide level unchanged
for r in out:
    r['adj'] = vol[r['team']] ** -(1 - VOL_LAMBDA) * _norm
    r['total_c'] = round(r['total'] - r['pts_attack'] * (1 - r['adj']), 2)
out.sort(key=lambda r: -r['total'])
json.dump(out, open(OUT / 'components.json', 'w'), indent=1)

# cross-check against the pipeline's own season file
S = {r['id']: sum(r['by_gw']) for r in
     json.load(open('v2/projections_season.json'))['players']}
d = [abs(r['total'] - S.get(r['id'], 0)) for r in out if r['id'] in S]
print(f'cross-check vs projections_season.json: max abs diff {max(d):.2f} pts, '
      f'mean {np.mean(d):.3f}')

print(f"\n{'player':<16}{'tm':<5}{'pos':<5}{'total':>7}{'atk':>7}{'CS':>6}"
      f"{'DefC':>6}{'app':>6}{'bon':>6}{'sav':>6}{'neg':>6}{'xG':>6}{'xA':>6}")
for r in out[:22]:
    print(f"{r['name']:<16}{r['team']:<5}{r['pos']:<5}{r['total']:>7.0f}"
          f"{r['pts_attack']:>7.0f}{r['pts_cs']:>6.0f}{r['pts_defcon']:>6.0f}"
          f"{r['pts_appear']:>6.0f}{r['pts_bonus']:>6.0f}{r['pts_saves']:>6.0f}"
          f"{-r['pts_neg']:>6.0f}{r['eg']*r['k']:>6.1f}{r['ea']*r['k']:>6.1f}")

tg = sum(r['eg'] for r in out); ta = sum(r['ea'] for r in out)
print(f'\nleague-wide expected goals from player rates: {tg:.0f} '
      f'(calibrated {sum(r["eg"]*r["k"] for r in out):.0f}); assists {ta:.0f}')
