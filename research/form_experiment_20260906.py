"""Chronological attacking-form experiment; no production forecast replay claim.

Train 2023/24, select on 2024/25, open 2025/26 once. Eligible players have
90+ minutes in the last three GW at the decision date. No future-minutes
filter or current-roster survivor filter. Predict actual goal/assist FPL
points over the next three GW (including zeros); minutes model is shared.
Actual historical fixture allocation is treated as known, so postponement
foreknowledge remains a limitation. This tests a component, not total FPL EV.
"""
import json
import gzip
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'research' / 'experiments-2026-09-06'
SEASONS = ['2022/23', '2023/24', '2024/25', '2025/26']
GP = {'DEF': 6, 'MID': 5, 'FWD': 4}


def build():
    cx = sqlite3.connect(ROOT / 'v2' / 'fpl.db')
    cx.row_factory = sqlite3.Row
    panel, fixtures = defaultdict(list), defaultdict(dict)
    for raw in cx.execute('SELECT * FROM gw_stat WHERE season < ? ORDER BY season,kickoff,fixture_id', ('2026/27',)):
        r = dict(raw)
        if r['season'] == '2022/23' and r['round'] < 16:
            continue  # unrecorded xG/starts, never zero-impute
        if r['pos'] not in GP:
            continue
        panel[r['season'], r['code']].append(r)
        fixtures[r['season'], r['team']][r['fixture_id']] = r['round']
    cx.close()
    records = []
    for season in SEASONS[1:]:
        previous = [s for s in SEASONS if s < season]
        prior_pos = defaultdict(lambda: np.zeros(3))
        for (s, _), rows in panel.items():
            if s not in previous:
                continue
            for r in rows:
                prior_pos[r['pos']] += [r['xg'] or 0, r['xa'] or 0, r['minutes']/90]
        priors = {p: a[:2]/max(a[2], 1) for p, a in prior_pos.items()}
        for (s, code), rows in panel.items():
            if s != season:
                continue
            hist = []
            for age, prev in enumerate(reversed(previous)):
                old = panel.get((prev, code), [])
                mins = sum(r['minutes'] for r in old)
                if mins >= 200:
                    w = .75**age
                    hist.append((w*sum((r['xg'] or 0) for r in old),
                                 w*sum((r['xa'] or 0) for r in old), w*mins/90))
            old_sum = np.sum(hist, axis=0) if hist else np.zeros(3)
            for cut in range(3, 36, 3):
                past = [r for r in rows if r['round'] <= cut]
                recent = [r for r in past if r['round'] > cut-3]
                recent_mins = sum(r['minutes'] for r in recent)
                if recent_mins < 90:
                    continue
                last = past[-1]
                pos, team = last['pos'], last['team']
                prior = priors[pos]
                now = np.array([sum((r['xg'] or 0) for r in past),
                                sum((r['xa'] or 0) for r in past),
                                sum(r['minutes'] for r in past)/90])
                future = [r for r in rows if cut < r['round'] <= cut+3]
                count = sum(cut < rd <= cut+3 for rd in fixtures[season, team].values())
                # Same strictly lagged exposure forecast for every rate model.
                tail = past[-6:]
                weights = np.array([.5**(j/3) for j in range(len(tail)-1, -1, -1)])
                exposure = np.average([r['minutes'] for r in tail], weights=weights)/90*count
                pseudo = np.array([2200*.15/90, 2200*((1-.84)/.84)/90])
                def rate(mult=1, continuous=False):
                    current = now if continuous or now[2]*90 >= 200 else np.zeros(3)
                    evidence = old_sum+mult*current
                    return (evidence[:2]+pseudo*prior)/(evidence[2]+pseudo)
                slow = rate()
                gp = np.array([GP[pos], 3])
                predictions = {'slow': float(slow@gp*exposure)}
                for mult in [2, 4, 8]:
                    predictions[f'current_x{mult}'] = float(rate(mult)@gp*exposure)
                predictions['continuous'] = float(rate(continuous=True)@gp*exposure)
                for hl in [3, 6, 12]:
                    w = np.array([.5**(j/hl) for j in range(len(past)-1, -1, -1)])
                    n = sum(w[j]*r['minutes']/90 for j,r in enumerate(past))
                    counts = np.array([sum(w[j]*(r[key] or 0) for j,r in enumerate(past))
                                       for key in ['xg', 'xa']])
                    for k in [3, 9, 18]:
                        fast = (counts+k*slow)/(n+k)
                        predictions[f'fast_h{hl}_k{k}'] = float(fast@gp*exposure)
                        # Innovation gate increases adaptation only when chance
                        # creation departs materially from the slow expectation.
                        # A variance floor regularizes tiny expected counts.
                        z2 = float(np.sum((counts-n*slow)**2/(n*np.maximum(slow, .05)+.25)))
                        for sensitivity in [.25, 1.0]:
                            q = 1-np.exp(-sensitivity*z2/2)
                            adaptive = (1-q)*slow+q*fast
                            predictions[f'gate_h{hl}_k{k}_c{sensitivity}'] = float(adaptive@gp*exposure)
                realised_rate = sum(GP[pos]*r['goals']+3*r['assists'] for r in recent)/(recent_mins/90)
                predictions['recent_returns'] = float(realised_rate*exposure)
                records.append(dict(season=season, code=code, cut=cut, pos=pos,
                                    low_form=recent_mins>=180 and sum(r['points'] for r in recent)<=6,
                                    y=sum(GP[pos]*r['goals']+3*r['assists'] for r in future),
                                    predictions=predictions))
        print('built', season, sum(r['season']==season for r in records), flush=True)
    return records


def metrics(rows, name, calibrations, subgroup=None):
    if subgroup is not None:
        rows = [r for r in rows if subgroup(r)]
    y = np.array([r['y'] for r in rows])
    p = np.array([r['predictions'][name]*calibrations[name][r['pos']] for r in rows])
    return dict(n=len(rows), mae=float(np.mean(abs(y-p))),
                rmse=float(np.sqrt(np.mean((y-p)**2))),
                bias=float(np.mean(p-y)), rho=float(spearmanr(y,p).statistic))


def main():
    records = build()
    names = list(records[0]['predictions'])
    train = [r for r in records if r['season']=='2023/24']
    valid = [r for r in records if r['season']=='2024/25']
    test = [r for r in records if r['season']=='2025/26']
    # Mean calibration learned on training season only; identical protocol.
    calibration = {name: {pos: sum(r['y'] for r in train if r['pos']==pos)/
                         max(1e-9,sum(r['predictions'][name] for r in train if r['pos']==pos))
                         for pos in GP} for name in names}
    validation = {name: metrics(valid,name,calibration) for name in names}
    winner = min(names, key=lambda name: validation[name]['rmse'])
    gate = min((n for n in names if n.startswith('gate')), key=lambda n:validation[n]['rmse'])
    # Choices frozen before evaluating held-out season.
    selected = list(dict.fromkeys(['slow','continuous','current_x4','recent_returns',winner,gate]))
    print('VALIDATION WINNER', winner, validation[winner], 'GATE', gate, flush=True)
    test_metrics = {name: metrics(test,name,calibration) for name in selected}
    low_form = {name:metrics(test,name,calibration,lambda r:r['low_form']) for name in selected}
    positions = {pos:{name:metrics(test,name,calibration,lambda r:r['pos']==pos)
                     for name in selected} for pos in GP}
    # Paired gameweek-block bootstrap: retain player dependence within weeks.
    rng = np.random.default_rng(20260906)
    cuts = sorted({r['cut'] for r in test})
    bootstrap = {}
    for name in selected:
        blocks = []
        for cut in cuts:
            rs = [r for r in test if r['cut']==cut]
            e0 = np.array([r['y']-r['predictions']['slow']*calibration['slow'][r['pos']] for r in rs])
            e1 = np.array([r['y']-r['predictions'][name]*calibration[name][r['pos']] for r in rs])
            blocks.append([sum(abs(e1)-abs(e0)),sum(e1**2-e0**2),len(rs)])
        blocks = np.array(blocks)
        draws = blocks[rng.integers(0,len(blocks),(4000,len(blocks)))].sum(axis=1)
        bootstrap[name] = dict(mae_delta_ci95=np.quantile(draws[:,0]/draws[:,2],[.025,.975]).tolist(),
                               mse_delta_ci95=np.quantile(draws[:,1]/draws[:,2],[.025,.975]).tolist())
    report = dict(method=__doc__, validation=validation, winner=winner, gate=gate,
                  test=test_metrics, low_form_test=low_form, position_test=positions,
                  calibration=calibration, paired_block_bootstrap=bootstrap)
    OUT.mkdir(exist_ok=True)
    (OUT/'form.json').write_text(json.dumps(report,indent=2)+'\n')
    with gzip.open(OUT/'form-predictions.json.gz','wt') as fh:
        json.dump(records,fh)
    print(json.dumps({k:report[k] for k in ['winner','gate','test','low_form_test','paired_block_bootstrap']},indent=2), flush=True)
    print('FORM DONE', flush=True)


if __name__ == '__main__':
    main()
