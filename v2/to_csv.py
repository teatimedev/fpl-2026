"""Write v2 projections in the v1 schema so the existing optimiser, validator
and web-app exporter run against them unchanged.

Writes BOTH data/projections.csv and data/projections.json, because they had
different consumers and only one of them was being kept current:

    projections.csv   -> optimise.py, simulate.py, plan.py
    projections.json  -> export_app_data.py, and therefore the web app

Only v1's project.py ever wrote the .json, and that is now guarded off, so the
app was quietly serving v1 numbers for every player while its suggested squads
came from v2. One writer for both files is the fix.
"""
import csv
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from gwclock import next_gw          # noqa: E402
ROOT = HERE.parent
SRC = HERE / 'projections_v2.json'
VIEW = HERE / 'season_view.json'
DST = ROOT / 'data' / 'projections.csv'
DST_JSON = ROOT / 'data' / 'projections.json'

FIELDS = ['id', 'name', 'full_name', 'team', 'team_id', 'pos', 'pos_id', 'price',
          'proj_gw', 'proj_6gw', 'proj_by_gw', 'mins_proj', 'value', 'sel_pct',
          'pts_last', 'mins_last', 'ppg_last', 'goals_last', 'assists_last',
          'xgi90_last', 'defcon_last', 'cs_last', 'bonus_last', 'status', 'news',
          'joined', 'is_new', 'pens', 'corners', 'fk', 'fdr6', 'cs_rate', 'note']

POS_ID = {'GKP': 1, 'DEF': 2, 'MID': 3, 'FWD': 4}


def main():
    data = json.loads(SRC.read_text())
    view = json.loads(VIEW.read_text())
    cx = sqlite3.connect(HERE / 'fpl.db')
    team_id = {r[1]: r[0] for r in cx.execute('SELECT id, short FROM team')}
    last = {}
    for row in cx.execute("""SELECT code, points, minutes, goals, assists,
                                    clean_sheets, bonus, defcon, xg, xa
                             FROM season_stat WHERE season='2025/26'"""):
        last[row[0]] = row[1:]
    code_of = {r[0]: r[1] for r in cx.execute('SELECT id, code FROM player')}
    cx.close()

    rows = []
    for p in data['players']:
        t = p['team']
        fixtures = view['view'].get(t, {})
        fdr6 = sum(f['fdr'] for g in fixtures.values() for f in g)
        cs_rate = (sum(f['cs'] for g in fixtures.values() for f in g)
                   / max(1, sum(len(g) for g in fixtures.values())))
        L = last.get(code_of.get(p['id']), (0,) * 9)
        pts, mins, g, a, cs, bonus, dc, xg, xa = L
        p90 = (mins or 0) / 90.0
        rows.append({
            'id': p['id'], 'name': p['name'], 'full_name': p['full_name'],
            'team': t, 'team_id': team_id.get(t, 0), 'pos': p['pos'],
            'pos_id': POS_ID[p['pos']], 'price': p['price'],
            'proj_gw': p['proj_gw'], 'proj_6gw': p['proj_6gw'],
            'proj_by_gw': p['proj_by_gw'], 'mins_proj': p['mins_proj'],
            'value': p['value'], 'sel_pct': p['sel_pct'],
            'pts_last': pts or 0, 'mins_last': mins or 0,
            'ppg_last': round((pts or 0) / 38.0, 2),
            'goals_last': g or 0, 'assists_last': a or 0,
            'xgi90_last': round(((xg or 0) + (xa or 0)) / p90, 3) if p90 else 0,
            'defcon_last': dc or 0, 'cs_last': cs or 0, 'bonus_last': bonus or 0,
            'status': p['status'], 'news': p['news'], 'joined': p['joined'],
            'is_new': p['joined'] >= '2026-05-01', 'pens': p['pens'] or '',
            'corners': p['corners'] or '', 'fk': p['fk'] or '',
            'fdr6': fdr6, 'cs_rate': round(cs_rate, 3), 'note': p.get('note', ''),
        })
    rows.sort(key=lambda r: -r['proj_6gw'])
    with open(DST, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    # the JSON twin, which is what the web-app exporter reads. The window rolls
    # (see gwclock.py): `horizon` is the last gameweek covered, `start_gw` the
    # first, and the schedule is indexed by absolute gameweek with nulls for
    # weeks already played, matching proj_by_gw.
    horizon = data.get('horizon') or (
        len(data['players'][0]['proj_by_gw']) if data['players'] else 6)
    start_gw = data.get('start_gw', 1)
    _, deadline = next_gw()
    schedule = {}
    for team, byweek in view['view'].items():
        schedule[team] = [
            ({'opp': byweek[str(g)][0]['opp'], 'home': byweek[str(g)][0]['home'],
              'fdr': byweek[str(g)][0]['fdr']} if byweek.get(str(g)) else None)
            for g in range(1, horizon + 1)]
    json.dump({'players': rows, 'schedule': schedule,
               'meta': {'horizon': horizon, 'start_gw': start_gw,
                        'budget': 100.0, 'deadline': deadline}},
              open(DST_JSON, 'w'))

    print(f'wrote {len(rows)} rows -> {DST.name} and {DST_JSON.name} '
          f'(v2 projections in v1 schema)')


if __name__ == '__main__':
    main()
