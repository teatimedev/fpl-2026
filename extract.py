import json, csv
d = json.load(open('data/bootstrap.json'))
teams = {t['id']: t['short_name'] for t in d['teams']}
pos = {1:'GKP',2:'DEF',3:'MID',4:'FWD'}
rows = []
for p in d['elements']:
    rows.append({
        'id': p['id'], 'name': p['web_name'],
        'full': (p['first_name']+' '+p['second_name']).strip(),
        'team': teams[p['team']], 'pos': pos[p['element_type']],
        'price': p['now_cost']/10,
        'sel%': float(p['selected_by_percent']),
        'pts_25_26': p['total_points'], 'mins': p['minutes'], 'starts': p['starts'],
        'ppg': float(p['points_per_game']),
        'g': p['goals_scored'], 'a': p['assists'],
        'xG': float(p['expected_goals']), 'xA': float(p['expected_assists']),
        'xGI90': p['expected_goal_involvements_per_90'],
        'xGC90': p['expected_goals_conceded_per_90'],
        'cs': p['clean_sheets'], 'bonus': p['bonus'], 'bps': p['bps'],
        'defcon': p['defensive_contribution'], 'defcon90': p['defensive_contribution_per_90'],
        'ict': float(p['ict_index']),
        'joined': p['team_join_date'], 'status': p['status'],
        'news': p['news'], 'cop_next': p['chance_of_playing_next_round'],
        'pens': p['penalties_order'], 'sp': p['corners_and_indirect_freekicks_order'],
        'fk': p['direct_freekicks_order'],
        'ep_next': p['ep_next'], 'born': p['birth_date'],
    })
rows.sort(key=lambda r: (-r['price'], -r['pts_25_26']))
with open('data/players.csv','w',newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print('wrote', len(rows), 'players')
