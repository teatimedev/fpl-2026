"""Bundle everything the web app needs into one JSON file."""
import json, csv, os
from datetime import datetime, timezone

CLUB_COLOURS = {
    'ARS': ('#EF0107', '#FFFFFF'), 'AVL': ('#95BFE5', '#670E36'),
    'BOU': ('#DA291C', '#000000'), 'BRE': ('#E30613', '#FBB800'),
    'BHA': ('#0057B8', '#FFCD00'), 'CHE': ('#034694', '#FFFFFF'),
    'COV': ('#78D0F3', '#000000'), 'CRY': ('#1B458F', '#C4122E'),
    'EVE': ('#003399', '#FFFFFF'), 'FUL': ('#FFFFFF', '#000000'),
    'HUL': ('#F5A12D', '#000000'), 'IPS': ('#3A64A3', '#DE2429'),
    'LEE': ('#FFCD00', '#1D428A'), 'LIV': ('#C8102E', '#00B2A9'),
    'MCI': ('#6CABDD', '#1C2C5B'), 'MUN': ('#DA291C', '#FBE122'),
    'NEW': ('#241F20', '#FFFFFF'), 'NFO': ('#DD0000', '#FFFFFF'),
    'SUN': ('#EB172B', '#211E1F'), 'TOT': ('#132257', '#FFFFFF'),
}

proj = json.load(open('data/projections.json'))
squads = json.load(open('data/squads.json'))
# v2's fetch caches the bootstrap it just pulled; v1's copy in data/ is frozen
# at 6 Aug and only kept for the v1 scripts
BOOT_PATH = ('v2/cache/bootstrap.json' if os.path.exists('v2/cache/bootstrap.json')
             else 'data/bootstrap.json')
boot = json.load(open(BOOT_PATH))

teams = {}
for t in boot['teams']:
    s = t['short_name']
    primary, secondary = CLUB_COLOURS.get(s, ('#888888', '#FFFFFF'))
    teams[s] = {'name': t['name'], 'short': s,
                'primary': primary, 'secondary': secondary}

# trim the player payload to what the UI actually renders
KEEP = ('id', 'name', 'full_name', 'team', 'pos', 'price', 'proj_gw', 'proj_6gw',
        'proj_by_gw',
        'mins_proj', 'sel_pct', 'pts_last', 'mins_last', 'ppg_last', 'goals_last',
        'assists_last', 'xgi90_last', 'defcon_last', 'cs_last', 'bonus_last',
        'status', 'news', 'is_new', 'joined', 'pens', 'corners', 'fk', 'note',
        'fdr6', 'value')
players = [{k: p[k] for k in KEEP} for p in proj['players']]

# the model's report card, graded by v2/scorecard.py once gameweeks finish
scorecard = (json.load(open('data/scorecard.json'))
             if os.path.exists('data/scorecard.json') else None)

out = {
    'meta': {**proj['meta'],
             'generated': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')},
    'teams': teams,
    'schedule': proj['schedule'],
    'players': players,
    'scorecard': scorecard,
    # optimise.py reads its pool from CSV, so ids arrive as strings -- coerce them
    # back to int so they match the player ids the app indexes on.
    'squads': [{'label': s['label'], 'cost': s['cost'], 'xi_proj': s['xi_proj'],
                'picks': [{'id': int(p['id']), 'starting': p['starting']}
                          for p in s['squad']]} for s in squads],
}
json.dump(out, open('app/src/data/fpl.json', 'w'), separators=(',', ':'))
print('players:', len(players), '| squads:', len(out['squads']),
      '| teams:', len(teams))
