"""Comparable recent evidence for owned players AND their proposed replacements."""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def summarise(pid, name, rows, completed):
    rows = [r for r in rows if int(r['fixture_id']) in completed]
    matches = [dict(gw=int(r['round']), minutes=int(r['minutes']), starts=int(r['starts']),
                    points=int(r['points']), goals=int(r['goals']), assists=int(r['assists']),
                    xg=round(float(r['xg']), 3), xa=round(float(r['xa']), 3),
                    penalties_missed=int(r['pens_missed'])) for r in rows]
    return dict(id=pid, name=name, matches=matches,
                source=f'https://fantasy.premierleague.com/api/element-summary/{pid}/',
                totals={key:round(sum(m[key] for m in matches), 3)
                        for key in ('minutes','starts','points','goals','assists','xg','xa','penalties_missed')})


def build(weekly, players, elements, fixtures):
    comparisons = [r for r in weekly.get('transfer_review', {}).get('players', [])
                   if players[r['player_id']]['name'] in ('Thiago', 'Kluivert') and r.get('replacement')]
    ids = {i for r in comparisons for i in (r['player_id'], r['replacement'])}
    completed = {f['id'] for f in fixtures if (f.get('finished') or f.get('finished_provisional'))
                 and f.get('team_h_score') is not None and f.get('team_a_score') is not None}
    codes = {elements[i]['code']: i for i in ids if i in elements}
    histories = {i: [] for i in ids}
    path = ROOT / 'data/gw_stats.csv'
    if not path.exists(): return None
    with path.open() as file:
        for row in csv.DictReader(file):
            pid = codes.get(int(row['code']))
            if pid is not None and row['season'] == '2026/27': histories[pid].append(row)
    return dict(gw=weekly['gw'], forecast_id=weekly.get('forecast_id'), generated=weekly['generated'],
                players=[summarise(i, players[i]['name'], histories[i], completed) for i in sorted(ids)],
                note='Completed fixtures only. Samples and opponents differ; total xG includes penalties. These are observations, not a fitted decline or a promise of future returns.')
