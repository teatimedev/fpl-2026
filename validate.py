"""Independent rules check on data/squads.json.

Deliberately does not import the optimiser -- it re-derives every constraint
from the official game rules and the raw API prices so a bug in the solver
cannot hide behind a bug in its own validation.
"""
import json, os, sys
from collections import Counter

# Prices must be the ones the optimiser priced against: v2's fetch writes this
# run's bootstrap to v2/cache/, and data/bootstrap.json is the v1 dump from
# 6 Aug 2026. The first refresh after prices started moving (27 Aug) failed
# on a £0.1m mismatch for exactly that reason.
BOOT_PATH = ('v2/cache/bootstrap.json' if os.path.exists('v2/cache/bootstrap.json')
             else 'data/bootstrap.json')
BOOT = json.load(open(BOOT_PATH))
PRICE = {p['id']: p['now_cost'] for p in BOOT['elements']}
POS = {p['id']: p['element_type'] for p in BOOT['elements']}
TEAM = {p['id']: p['team'] for p in BOOT['elements']}
STATUS = {p['id']: p['status'] for p in BOOT['elements']}
NAME = {p['id']: p['web_name'] for p in BOOT['elements']}

SQUAD_SHAPE = {1: 2, 2: 5, 3: 5, 4: 3}
XI_MIN = {1: 1, 2: 3, 3: 2, 4: 1}
XI_MAX = {1: 1, 2: 5, 3: 5, 4: 3}
PN = {1: 'GKP', 2: 'DEF', 3: 'MID', 4: 'FWD'}

fails = []
warns = []

for res in json.load(open('data/squads.json')):
    lbl = res['label'].split(' - ')[0]
    squad = res['squad']
    ids = [int(p['id']) for p in squad]

    def bad(msg):
        fails.append(f'{lbl}: {msg}')

    # unique players
    if len(set(ids)) != 15:
        bad(f'squad has {len(set(ids))} unique players, expected 15')

    # squad shape from the API's own position data
    shape = Counter(POS[i] for i in ids)
    for et, n in SQUAD_SHAPE.items():
        if shape.get(et, 0) != n:
            bad(f'{PN[et]}: has {shape.get(et,0)}, needs {n}')

    # budget, recomputed from raw API prices
    cost = sum(PRICE[i] for i in ids)
    if cost > 1000:
        bad(f'cost £{cost/10}m exceeds the £100.0m budget')
    if abs(cost / 10 - res['cost']) > 1e-6:
        bad(f'reported cost £{res["cost"]}m != API cost £{cost/10}m')

    # max 3 per club
    for team, n in Counter(TEAM[i] for i in ids).items():
        if n > 3:
            bad(f'{n} players from team id {team}, max is 3')

    # legal starting XI
    xi = [int(p['id']) for p in squad if p['starting']]
    if len(xi) != 11:
        bad(f'starting XI has {len(xi)} players, needs 11')
    xshape = Counter(POS[i] for i in xi)
    for et in SQUAD_SHAPE:
        n = xshape.get(et, 0)
        if not (XI_MIN[et] <= n <= XI_MAX[et]):
            bad(f'XI {PN[et]}: {n} outside legal {XI_MIN[et]}-{XI_MAX[et]}')

    # availability sanity -- flag anyone unavailable, loudly if they're starting
    for i in ids:
        st = STATUS[i]
        if st == 'u':
            bad(f'{NAME[i]} has left the club (status u) but is in the squad')
        elif st in ('i', 's') and i in xi:
            warns.append(f'{lbl}: {NAME[i]} is flagged "{st}" but is in the XI')
        elif st in ('i', 's'):
            warns.append(f'{lbl}: {NAME[i]} is flagged "{st}" (on the bench)')

    print(f'{lbl:<9} {cost/10:>6}m  XI={len(xi)}  '
          f'shape={"-".join(str(xshape.get(e,0)) for e in (2,3,4))}  '
          f'clubs_ok={max(Counter(TEAM[i] for i in ids).values())<=3}')

print()
for w in warns:
    print('WARN ', w)
if fails:
    print()
    for f in fails:
        print('FAIL ', f)
    sys.exit(1)
print('\nAll squads pass the FPL 2026/27 rules.')
