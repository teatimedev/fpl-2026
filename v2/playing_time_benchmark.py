"""Chronological component experiment; see the prewritten research protocol.

Run --select first (2023/24), then --evaluate after selection is frozen.
Historical availability and authentic deadline snapshots are not present.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import sqlite3

import numpy as np

try:
    from .playing_time import fit_priors, estimate
except ImportError:
    from playing_time import fit_priors, estimate

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'research/system-audit-2026-09-12'


def panel(db):
    cx = sqlite3.connect(f'file:{Path(db).resolve()}?mode=ro', uri=True)
    cx.row_factory = sqlite3.Row
    rows = [dict(r) for r in cx.execute(
        "SELECT code,season,round,fixture_id,kickoff,pos,minutes,starts "
        "FROM gw_stat WHERE season < '2026/27' AND pos IN ('GKP','DEF','MID','FWD') "
        "ORDER BY season,round,kickoff,fixture_id,code")]
    cx.close()
    for r in rows:
        r['time'] = datetime.fromisoformat(r['kickoff'].replace('Z', '+00:00'))
    return rows


def forecast_cases(rows, season):
    """Freeze prior observations at the same boundary for the entire GW."""
    selected = [r for r in rows if r['season'] == season]
    by_round = defaultdict(list)
    for r in selected:
        by_round[r['round']].append(r)
    cases = []
    for gw, targets in sorted(by_round.items()):
        cutoff = min(r['time'] for r in targets) - timedelta(hours=2)
        history = defaultdict(list)
        for r in selected:
            if r['round'] < gw and r['time'] + timedelta(hours=3) < cutoff:
                history[r['code']].append(r)
        for r in targets:
            if r['starts'] is None:
                continue
            past = sorted(history[r['code']], key=lambda x: (x['time'], x['fixture_id']), reverse=True)
            evidence = [(age, h['starts'], h['minutes']) for age, h in enumerate(past)]
            active = any(h['minutes'] > 0 for h in past[:6])
            cases.append((r, evidence, active))
    return cases


def predict(cases, priors, configuration):
    out = []
    for r, evidence, active in cases:
        prior = priors[r['pos']]
        if configuration['name'] == 'legacy':
            prediction = dict(p_cameo=0 if r['pos'] == 'GKP' else .2,
                              p60_start=1., p60_cameo=0.,
                              start_minutes=prior['start_minutes'], cameo_minutes=25.)
        elif configuration['name'] == 'positional':
            prediction = prior
        else:
            prediction = estimate(prior, evidence, configuration['strength'],
                                  configuration['half_life'] or math.inf)
        started, played = bool(r['starts']), r['minutes'] > 0
        out.append(dict(gw=r['round'], code=r['code'], active=active,
                        pos=r['pos'], started=started, played=played,
                        cameo_error=None if started else (prediction['p_cameo'] - played) ** 2,
                        p60_error=(prediction['p60_start'] - (r['minutes'] >= 60)) ** 2 if started else None,
                        start_minutes_error=prediction['start_minutes'] - r['minutes'] if started else None,
                        cameo_minutes_error=prediction['cameo_minutes'] - r['minutes'] if played and not started else None,
                        p_cameo=prediction['p_cameo'], p60_start=prediction['p60_start'],
                        actual_p60=r['minutes'] >= 60))
    return out


def summarize(rows):
    def metric(key, squared=False):
        values = [r[key] for r in rows if r[key] is not None]
        if not values:
            return {'n': 0, 'mean': None}
        return {'n': len(values), 'mean': float(np.mean(values)),
                **({'mae': float(np.mean(np.abs(values))),
                    'rmse': float(np.sqrt(np.mean(np.square(values))))} if squared else {})}
    result = {key: metric(key, key.endswith('minutes_error')) for key in (
        'cameo_error', 'p60_error', 'start_minutes_error', 'cameo_minutes_error')}
    nonstarts = [r for r in rows if not r['started']]
    starts = [r for r in rows if r['started']]
    result['calibration'] = {
        'cameo_pred': float(np.mean([r['p_cameo'] for r in nonstarts])),
        'cameo_actual': float(np.mean([r['played'] for r in nonstarts])),
        'p60_pred': float(np.mean([r['p60_start'] for r in starts])),
        'p60_actual': float(np.mean([r['actual_p60'] for r in starts])),
    }
    return result


def block_interval(candidate, baseline, key):
    blocks = defaultdict(list)
    for c, b in zip(candidate, baseline):
        assert (c['code'], c['gw']) == (b['code'], b['gw'])
        if c['active'] and c[key] is not None:
            blocks[c['gw']].append(c[key] - b[key])
    sums = np.array([sum(x) for x in blocks.values()])
    counts = np.array([len(x) for x in blocks.values()])
    rng = np.random.default_rng(20260912)
    samples = rng.integers(0, len(sums), (2000, len(sums)))
    deltas = sums[samples].sum(axis=1) / counts[samples].sum(axis=1)
    return {'difference': float(sums.sum()/counts.sum()),
            'interval95': [float(x) for x in np.quantile(deltas, [.025, .975])],
            'gameweek_blocks': len(sums)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=str(ROOT / 'v2/fpl.db'))
    parser.add_argument('--select', action='store_true')
    parser.add_argument('--evaluate', action='store_true')
    args = parser.parse_args()
    if args.select == args.evaluate:
        parser.error('Choose exactly one of --select or --evaluate')
    rows = panel(args.db)
    priors = fit_priors([r for r in rows if r['season'] == '2022/23'])
    OUT.mkdir(parents=True, exist_ok=True)
    selected_path = OUT / 'playing-time-selection.json'
    if args.select:
        candidates = [{'name': 'legacy'}, {'name': 'positional'}] + [
            {'name': f'player-k{k}-hl{hl}', 'strength': k, 'half_life': hl}
            for k in (4., 8., 16.) for hl in (6., 12., None)]
        cases = forecast_cases(rows, '2023/24')
        results = []
        for config in candidates:
            pred = predict(cases, priors, config)
            stats = summarize([r for r in pred if r['active']])
            objective = stats['cameo_error']['mean'] + stats['p60_error']['mean']
            results.append({'configuration': config, 'objective': objective, 'active': stats})
        chosen = min((r for r in results if r['configuration']['name'] != 'legacy'), key=lambda r: r['objective'])
        result = dict(training='2022/23 recorded rows', selection='2023/24', priors=priors,
                      selected=chosen['configuration'], results=results)
        selected_path.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
        print(json.dumps({'selected': result['selected'], 'results': [
            {'name': r['configuration']['name'], 'objective': r['objective']} for r in results]}, indent=2))
    else:
        selection = json.loads(selected_path.read_text())
        result = {'selection': selection['selected'], 'seasons': {}}
        for season in ('2024/25', '2025/26'):
            cases = forecast_cases(rows, season)
            predictions = {c['name']: predict(cases, selection['priors'], c)
                           for c in ({'name': 'legacy'}, {'name': 'positional'}, selection['selected'])}
            selected = predictions[selection['selected']['name']]
            result['seasons'][season] = {
                'metrics': {name: {'all': summarize(pred), 'active': summarize([r for r in pred if r['active']])}
                            for name, pred in predictions.items()},
                'paired_active': {baseline: {key: block_interval(selected, predictions[baseline], key)
                                             for key in ('cameo_error', 'p60_error')}
                                  for baseline in ('legacy', 'positional')},
            }
        (OUT / 'playing-time-evaluation.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
