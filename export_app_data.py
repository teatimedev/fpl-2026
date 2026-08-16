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
        'fdr6', 'value', 'pts_now', 'mins_now', 'starts_now', 'games_now',
        'xg90', 'xa90', 'dc90', 'start_rate', 'evidence', 'seasons')
players = [{k: p.get(k) for k in KEEP} for p in proj['players']]

# per-player season curve (coarse, minutes held constant) for the player drawer
# and chip context — one decimal is plenty and keeps the bundle small
if os.path.exists('v2/projections_season.json'):
    season = {p['id']: p['by_gw'] for p in json.load(open('v2/projections_season.json'))['players']}
    for p in players:
        p['season_by_gw'] = [round(x, 1) for x in season.get(p['id'], [])]

# the model's fixture ticker: every remaining gameweek for every club, with
# clean-sheet probability and expected goals (not FDR) — from v2/season_view.json
ticker = {}
if os.path.exists('v2/season_view.json'):
    sv = json.load(open('v2/season_view.json'))
    start = sv.get('start_gw', 1)
    for team, byweek in sv['view'].items():
        ticker[team] = [
            dict(gw=int(g), fx=[dict(opp=f['opp'], home=f['home'], cs=round(f['cs'], 3),
                                     xg=round(f['xg'], 2), xgc=round(f['xgc'], 2),
                                     src=f.get('src', 'model')) for f in fx])
            for g, fx in sorted(byweek.items(), key=lambda kv: int(kv[0])) if int(g) >= start]

# the structured digest (weekly.py --json) and the crowd's movements (movers.py)
weekly = json.load(open('data/weekly.json')) if os.path.exists('data/weekly.json') else None
if weekly:
    weekly.pop('digest_md', None)          # the app renders data, not markdown
movers = json.load(open('data/movers.json')) if os.path.exists('data/movers.json') else None

# the model's report card, graded by v2/scorecard.py once gameweeks finish
scorecard = (json.load(open('data/scorecard.json'))
             if os.path.exists('data/scorecard.json') else None)
# chip valuation for the digest squad (v2/chips.py via weekly.py --chips)
chips = json.load(open('data/chips.json')) if os.path.exists('data/chips.json') else None

out = {
    'meta': {**proj['meta'],
             'generated': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')},
    'teams': teams,
    'schedule': proj['schedule'],
    'players': players,
    'scorecard': scorecard,
    'chips': chips,
    'weekly': weekly,
    'movers': movers,
    'ticker': ticker,
    # optimise.py reads its pool from CSV, so ids arrive as strings -- coerce them
    # back to int so they match the player ids the app indexes on.
    'squads': [{'label': s['label'], 'cost': s['cost'], 'xi_proj': s['xi_proj'],
                'picks': [{'id': int(p['id']), 'starting': p['starting']}
                          for p in s['squad']]} for s in squads],
}
json.dump(out, open('app/src/data/fpl.json', 'w'), separators=(',', ':'))
print('players:', len(players), '| squads:', len(out['squads']),
      '| teams:', len(teams))
