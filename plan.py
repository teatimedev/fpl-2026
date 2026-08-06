"""
Multi-period transfer planner for GW1-6.

A static squad is not how the game is played: you get one free transfer a week,
you can bank up to five, and extra transfers cost 4 points each. That turns team
selection into a planning problem rather than a one-shot pick — a player with a
great GW1-3 and a terrible GW4-6 is worth buying and then selling.

This solves the whole horizon at once as a single integer program:

  variables   x[p][gw]  player p is in the squad for gameweek gw
              y[p][gw]  ... and in the starting XI
              c[p][gw]  ... and captained
              in/out    transfers made before gameweek gw
  objective   sum of starting-XI points, captain counted twice, minus 4 per hit
  subject to  £100.0m and 2/5/5/3 and max-3-per-club IN EVERY GAMEWEEK,
              a legal XI in every gameweek, and FPL's free-transfer accounting
              (one per week, bank up to five).

Assumptions worth knowing: prices are held static across the horizon, and FPL's
sell-price rule (you only bank half of any rise) is not modelled. Both are minor
over six weeks but they make the plan slightly optimistic.
"""
import csv, json, argparse
import pulp

HORIZON = 6
BUDGET = 1000
SQUAD = {'GKP': 2, 'DEF': 5, 'MID': 5, 'FWD': 3}
XI_MIN = {'GKP': 1, 'DEF': 3, 'MID': 2, 'FWD': 1}
XI_MAX = {'GKP': 1, 'DEF': 5, 'MID': 5, 'FWD': 3}
HIT = 4.0
MAX_BANK = 5
POOL = 130


def load():
    rows = []
    for r in csv.DictReader(open('data/projections.csv')):
        r['price_t'] = int(round(float(r['price']) * 10))
        r['id'] = int(r['id'])
        r['gw'] = [float(x) for x in
                   r['proj_by_gw'].strip('[]').replace("'", '').split(',')]
        r['proj_6gw'] = float(r['proj_6gw'])
        rows.append(r)
    return rows


def build_pool(players, must_include):
    """Keep the problem tractable: the best N players plus anyone already owned."""
    keep = {p['id'] for p in sorted(players, key=lambda r: -r['proj_6gw'])[:POOL]}
    keep |= set(must_include)
    return [p for p in players if p['id'] in keep]


def plan(players, seed=None, allow_hits=True, allow_transfers=True, label=''):
    """Optimise GW1-6. If `seed` is given, the GW1 squad is fixed to it."""
    pool = build_pool(players, seed or [])
    ids = [p['id'] for p in pool]
    P = {p['id']: p for p in pool}
    GW = range(1, HORIZON + 1)

    prob = pulp.LpProblem('fpl_plan', pulp.LpMaximize)
    x = {(i, g): pulp.LpVariable(f'x{i}_{g}', cat='Binary') for i in ids for g in GW}
    y = {(i, g): pulp.LpVariable(f'y{i}_{g}', cat='Binary') for i in ids for g in GW}
    c = {(i, g): pulp.LpVariable(f'c{i}_{g}', cat='Binary') for i in ids for g in GW}
    tin = {(i, g): pulp.LpVariable(f'i{i}_{g}', cat='Binary')
           for i in ids for g in range(2, HORIZON + 1)}
    tout = {(i, g): pulp.LpVariable(f'o{i}_{g}', cat='Binary')
            for i in ids for g in range(2, HORIZON + 1)}
    hits = {g: pulp.LpVariable(f'h{g}', lowBound=0, cat='Integer')
            for g in range(2, HORIZON + 1)}
    bank = {g: pulp.LpVariable(f'b{g}', lowBound=0, upBound=MAX_BANK, cat='Integer')
            for g in range(2, HORIZON + 2)}

    # ---- objective: XI points, captain twice, minus hits --------------
    prob += (pulp.lpSum(y[(i, g)] * P[i]['gw'][g - 1] for i in ids for g in GW)
             + pulp.lpSum(c[(i, g)] * P[i]['gw'][g - 1] for i in ids for g in GW)
             - HIT * pulp.lpSum(hits[g] for g in range(2, HORIZON + 1)))

    for g in GW:
        # squad shape and budget hold in EVERY gameweek, not just the first
        prob += pulp.lpSum(x[(i, g)] for i in ids) == 15
        for pos, n in SQUAD.items():
            prob += pulp.lpSum(x[(i, g)] for i in ids if P[i]['pos'] == pos) == n
        prob += pulp.lpSum(x[(i, g)] * P[i]['price_t'] for i in ids) <= BUDGET
        for club in {p['team'] for p in pool}:
            prob += pulp.lpSum(x[(i, g)] for i in ids if P[i]['team'] == club) <= 3
        # starting XI
        prob += pulp.lpSum(y[(i, g)] for i in ids) == 11
        for pos in SQUAD:
            n = pulp.lpSum(y[(i, g)] for i in ids if P[i]['pos'] == pos)
            prob += n >= XI_MIN[pos]
            prob += n <= XI_MAX[pos]
        prob += pulp.lpSum(c[(i, g)] for i in ids) == 1
        for i in ids:
            prob += y[(i, g)] <= x[(i, g)]
            prob += c[(i, g)] <= y[(i, g)]

    # ---- transfer linking ---------------------------------------------
    for g in range(2, HORIZON + 1):
        for i in ids:
            prob += x[(i, g)] - x[(i, g - 1)] == tin[(i, g)] - tout[(i, g)]
            prob += tin[(i, g)] + tout[(i, g)] <= 1
        n_out = pulp.lpSum(tout[(i, g)] for i in ids)
        # free-transfer accounting: one per week, bank up to five
        prev = 1 if g == 2 else bank[g]
        prob += hits[g] >= n_out - prev
        if not allow_hits:
            prob += hits[g] == 0
        if not allow_transfers:
            prob += n_out == 0          # the hold-all-15 baseline
        prob += bank[g + 1] <= prev - n_out + hits[g] + 1
        prob += bank[g + 1] <= MAX_BANK

    if seed:
        for i in seed:
            if i in P:
                prob += x[(i, 1)] == 1

    prob.solve(pulp.HiGHS(msg=False, timeLimit=120))
    if pulp.LpStatus[prob.status] not in ('Optimal', 'Not Solved'):
        return None

    out = {'label': label, 'weeks': [], 'total': 0.0, 'hits': 0}
    for g in GW:
        squad = [i for i in ids if x[(i, g)].value() > 0.5]
        xi = [i for i in ids if y[(i, g)].value() > 0.5]
        cap = [i for i in ids if c[(i, g)].value() > 0.5]
        pts = sum(P[i]['gw'][g - 1] for i in xi) + sum(P[i]['gw'][g - 1] for i in cap)
        h = int(round(hits[g].value())) if g > 1 else 0
        out['total'] += pts - HIT * h
        out['hits'] += h
        out['weeks'].append({
            'gw': g, 'pts': pts, 'hits': h,
            'squad': squad, 'xi': xi, 'captain': cap[0] if cap else None,
            'in': [i for i in ids if g > 1 and tin[(i, g)].value() > 0.5],
            'out': [i for i in ids if g > 1 and tout[(i, g)].value() > 0.5],
            'cost': sum(P[i]['price_t'] for i in squad) / 10,
        })
    out['_P'] = P
    return out


def show(res):
    P = res['_P']
    print(f"\n{'='*74}\n  {res['label']}")
    print(f"  total {res['total']:.1f} pts over GW1-{HORIZON}  "
          f"({res['hits']} hit{'s' if res['hits'] != 1 else ''} taken)")
    print('='*74)
    for w in res['weeks']:
        moves = ''
        if w['in']:
            moves = '   ' + ', '.join(
                f"{P[o]['name']} -> {P[i]['name']}"
                for i, o in zip(w['in'], w['out']))
            if w['hits']:
                moves += f"  (-{w['hits'] * 4})"
        print(f"  GW{w['gw']}  {w['pts']:>5.1f} pts   C: {P[w['captain']]['name']:<14}"
              f" £{w['cost']:.1f}m{moves}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--squad', default=None,
                    help='index into data/squads.json to seed GW1 from')
    args = ap.parse_args()

    players = load()
    squads = json.load(open('data/squads.json'))
    results = []

    # 1. free planning: choose the GW1 squad WITH foresight of the transfer path
    r = plan(players, label='PLANNED — GW1 squad chosen knowing the transfer path')
    results.append(r)

    # 2. each candidate squad, played optimally
    for s in squads:
        seed = [int(p['id']) for p in s['squad']]
        nick = s['label'].split('"')[1] if '"' in s['label'] else s['label']
        lbl = s['label'].split(' - ')[0].replace('OPTION ', 'Option ')
        results.append(plan(players, seed=seed, label=f'{lbl} ({nick}) played optimally'))

    # 3. the same squads held for all six gameweeks -- the baseline that shows
    #    what the transfers are actually worth
    holds = {}
    for s in squads:
        seed = [int(p['id']) for p in s['squad']]
        lbl = s['label'].split(' - ')[0].replace('OPTION ', 'Option ')
        holds[lbl] = plan(players, seed=seed, allow_transfers=False,
                          label=f'{lbl} held for all six gameweeks')

    for r in results:
        if r:
            show(r)

    print(f"\n{'='*74}\n  WHAT THE TRANSFERS ARE WORTH\n{'='*74}")
    print(f"  {'squad':<26}{'held':>9}{'played':>9}{'gain':>8}{'hits':>7}")
    for r in results[1:]:
        key = r['label'].split(' (')[0]
        h = holds.get(key)
        if not h:
            continue
        print(f"  {key:<26}{h['total']:>9.1f}{r['total']:>9.1f}"
              f"{r['total'] - h['total']:>+8.1f}{r['hits']:>7}")
    free = results[0]
    best_held = max(h['total'] for h in holds.values())
    print(f"\n  Planning the GW1 squad with the transfer path in mind: "
          f"{free['total']:.1f} pts")
    print(f"  Best squad simply held:                              "
          f"{best_held:.1f} pts")
    print(f"  Difference:                                          "
          f"{free['total'] - best_held:+.1f} pts")
