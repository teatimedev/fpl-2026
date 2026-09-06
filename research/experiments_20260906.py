"""Reproducible audit experiments. Never writes production data or sends advice.

Usage: .venv/bin/python -u research/experiments_20260906.py snapshot|threshold
Outputs are isolated under research/experiments-2026-09-06 (raw API in /tmp).
"""
import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'v2'))
OUT = ROOT / 'research' / 'experiments-2026-09-06'
LAB = Path('/tmp/fpl-experiments-2026-09-06')


def save(name, value):
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(json.dumps(value, indent=2, default=float) + '\n')


def snapshot():
    import fetch
    LAB.mkdir(exist_ok=True)
    cache = LAB / 'cache'
    cache.mkdir(exist_ok=True)
    db = LAB / 'fpl.db'
    if not db.exists():
        shutil.copy2(ROOT / 'v2' / 'fpl.db', db)
    fetch.DB, fetch.CACHE = db, cache
    fetch.GW_STATS_CSV = LAB / 'gw_stats.csv'
    export = fetch.export_gw_stats
    fetch.export_gw_stats = lambda connection: export(connection, path=fetch.GW_STATS_CSV)
    cx = sqlite3.connect(db)
    boot = fetch.load_fpl(cx)
    fixtures = json.loads((cache / 'fixtures.json').read_text())
    fetch.load_histories(cx, boot)
    fetch.load_current_season(cx, boot, fixtures)
    cx.commit()
    entry = fetch.get(f'{fetch.FPL}/entry/3415101/')
    history = fetch.get(f'{fetch.FPL}/entry/3415101/history/')
    picks = fetch.get(f'{fetch.FPL}/entry/3415101/event/3/picks/')
    transfers = fetch.get(f'{fetch.FPL}/entry/3415101/transfers/')
    evidence = dict(at=datetime.now(timezone.utc).isoformat(), entry=entry,
                    history=history, picks=picks, transfers=transfers)
    (LAB / 'account.json').write_text(json.dumps(evidence))
    owned = {p['element'] for p in picks['picks']}
    details = []
    for p in boot['elements']:
        if p['id'] not in owned:
            continue
        rows = cx.execute('SELECT round,minutes,starts,points,goals,assists,xg,xa,pens_missed '
                          'FROM gw_stat WHERE season=? AND code=? ORDER BY round',
                          ('2026/27', p['code'])).fetchall()
        details.append(dict(id=p['id'], name=p['web_name'], price=p['now_cost']/10,
                            minutes=p['minutes'], points=p['total_points'],
                            status=p['status'], news=p['news'], games=rows))
    save('live-squad.json', dict(at=evidence['at'], players=details,
                               history=history, transfers=transfers,
                               chip=picks['active_chip']))
    cx.close()
    print('SNAPSHOT DONE', evidence['at'], flush=True)


def old_totals(g, z, sigma, thresholds):
    """Frozen, faulty pre-audit rule, retained ONLY for paired comparison."""
    out = []
    for tau in thresholds:
        ft = np.ones(len(g))
        total = np.zeros(len(g))
        for t in range(g.shape[1]):
            hit = ft < 1
            act = g[:, t] + sigma*z[:, t] - 4*hit >= tau
            total += np.where(act, g[:, t] - 4*hit, 0)
            ft = np.where(act, np.maximum(ft-1, 0), np.minimum(ft+1, 5))
        out.append(total)
    return np.asarray(out)


def threshold():
    import threshold_sweep as sweep
    rng = np.random.default_rng(20260906)
    g = sweep.draw_gains(rng, (20000, 38))
    z = rng.standard_normal(g.shape)
    rows = []
    for sigma in [0, 1.5, 2.3, 3.5, 5.16]:
        for name, fn in [('old_bug', old_totals), ('corrected', sweep.simulate_totals)]:
            totals = fn(g, z, sigma, sweep.THRESHOLDS)
            means = totals.mean(axis=1)
            best = int(means.argmax())
            k2 = int(np.argmin(abs(sweep.THRESHOLDS-2)))
            diff = totals[best] - totals[k2]
            row = dict(rule=name, sigma=sigma, seasons=len(g),
                       best_threshold=float(sweep.THRESHOLDS[best]),
                       best_mean=means[best], threshold_2_mean=means[k2],
                       best_minus_2=diff.mean(),
                       paired_ci95=[diff.mean()-1.96*diff.std(ddof=1)/len(g)**.5,
                                    diff.mean()+1.96*diff.std(ddof=1)/len(g)**.5],
                       curve=means.tolist())
            rows.append(row)
            print({k:v for k,v in row.items() if k!='curve'}, flush=True)
    save('threshold.json', rows)
    print('THRESHOLD DONE', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('experiment', choices=['snapshot', 'threshold'])
    args = parser.parse_args()
    globals()[args.experiment]()
