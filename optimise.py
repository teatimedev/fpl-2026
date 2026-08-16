"""
FPL 2026/27 squad optimiser.

Solves for a 15-player squad under the real game constraints:
  - £100.0m budget
  - 2 GKP, 5 DEF, 5 MID, 3 FWD
  - maximum 3 players per club
  - a legal starting XI (1 GKP, 3-5 DEF, 2-5 MID, 1-3 FWD)

The objective maximises the projected points of the STARTING XI plus a small
weight on the bench, which is what actually matters: bench players only score
if someone in the XI does not play. That weighting is what produces the classic
"stack the XI, cheap bench" shape rather than 15 evenly-priced players.
"""
import csv, json, argparse
import pulp

BUDGET = 1000        # tenths of a million
SQUAD = {1: 2, 2: 5, 3: 5, 4: 3}
XI_MIN = {1: 1, 2: 3, 3: 2, 4: 1}
XI_MAX = {1: 1, 2: 5, 3: 5, 4: 3}
POS_ID = {'GKP': 1, 'DEF': 2, 'MID': 3, 'FWD': 4}
BENCH_WEIGHT = 0.12  # a bench point is worth ~1/8 of a starting point


def load():
    rows = []
    for r in csv.DictReader(open('data/projections.csv')):
        r['price_t'] = int(round(float(r['price']) * 10))
        r['proj'] = float(r['proj_6gw'])
        r['pos_id'] = POS_ID[r['pos']]
        r['mins_proj'] = int(r['mins_proj'])
        r['sel_pct'] = float(r['sel_pct'])
        rows.append(r)
    return rows


def solve(players, banned=(), forced=(), max_per_club=3, budget=BUDGET,
          min_sel=None, max_sel=None, exclude_squads=(), label='',
          max_def_spend=None, min_att_spend=None):
    """Return one optimal squad. `exclude_squads` blocks previously found
    solutions so we can generate genuinely different options."""
    pool = [p for p in players if p['id'] not in banned]

    prob = pulp.LpProblem('fpl', pulp.LpMaximize)
    pick = {p['id']: pulp.LpVariable(f"p{p['id']}", cat='Binary') for p in pool}
    start = {p['id']: pulp.LpVariable(f"s{p['id']}", cat='Binary') for p in pool}

    prob += pulp.lpSum(
        start[p['id']] * p['proj'] * (1 - BENCH_WEIGHT)
        + pick[p['id']] * p['proj'] * BENCH_WEIGHT
        for p in pool)

    # squad shape
    for et, n in SQUAD.items():
        prob += pulp.lpSum(pick[p['id']] for p in pool if p['pos_id'] == et) == n
    prob += pulp.lpSum(pick[p['id']] * p['price_t'] for p in pool) <= budget
    prob += pulp.lpSum(pick[p['id']] for p in pool) == 15

    # starting XI shape
    prob += pulp.lpSum(start[p['id']] for p in pool) == 11
    for et in SQUAD:
        n = pulp.lpSum(start[p['id']] for p in pool if p['pos_id'] == et)
        prob += n >= XI_MIN[et]
        prob += n <= XI_MAX[et]
    for p in pool:
        prob += start[p['id']] <= pick[p['id']]

    # max 3 per club
    for club in {p['team'] for p in pool}:
        prob += pulp.lpSum(pick[p['id']] for p in pool if p['team'] == club) <= max_per_club

    for pid in forced:
        if pid in pick:
            prob += pick[pid] == 1

    # Structural constraints used to test the conventional "cheap defence,
    # premium attack" shape against the model's own preference.
    if max_def_spend is not None:
        prob += pulp.lpSum(pick[p['id']] * p['price_t'] for p in pool
                           if p['pos_id'] in (1, 2)) <= max_def_spend
    if min_att_spend is not None:
        prob += pulp.lpSum(pick[p['id']] * p['price_t'] for p in pool
                           if p['pos_id'] in (3, 4)) >= min_att_spend

    # ownership band (used to build a differential squad)
    if max_sel is not None:
        for p in pool:
            if p['sel_pct'] > max_sel:
                prob += pick[p['id']] == 0
    if min_sel is not None:
        prob += pulp.lpSum(pick[p['id']] for p in pool
                           if p['sel_pct'] >= min_sel) >= 6

    # force diversity from previously generated squads
    for prev in exclude_squads:
        ids = [i for i in prev if i in pick]
        prob += pulp.lpSum(pick[i] for i in ids) <= len(ids) - 5

    # PuLP ships an x86-only CBC binary, which will not run on Apple Silicon.
    prob.solve(pulp.HiGHS(msg=False))
    if pulp.LpStatus[prob.status] != 'Optimal':
        return None

    xi = {p['id'] for p in pool if start[p['id']].value() > 0.5}
    # copy each player: the pool dicts are shared across solves, so mutating them
    # in place would let a later solution overwrite an earlier one's XI.
    squad = [dict(p, starting=(p['id'] in xi))
             for p in pool if pick[p['id']].value() > 0.5]
    return {'label': label, 'squad': squad,
            'cost': sum(p['price_t'] for p in squad) / 10,
            'xi_proj': round(sum(p['proj'] for p in squad if p['starting']), 1),
            'squad_proj': round(sum(p['proj'] for p in squad), 1)}


ORDER = {'GKP': 0, 'DEF': 1, 'MID': 2, 'FWD': 3}


def show(res):
    s = res['squad']
    xi = sorted([p for p in s if p['starting']],
                key=lambda p: (ORDER[p['pos']], -p['proj']))
    bench = sorted([p for p in s if not p['starting']],
                   key=lambda p: (ORDER[p['pos']], -p['proj']))
    nd = sum(1 for p in xi if p['pos'] == 'DEF')
    nm = sum(1 for p in xi if p['pos'] == 'MID')
    nf = sum(1 for p in xi if p['pos'] == 'FWD')
    cap = max(xi, key=lambda p: p['proj'])
    vice = sorted(xi, key=lambda p: -p['proj'])[1]

    print(f"\n{'='*78}")
    print(f"  {res['label']}")
    print(f"  formation {nd}-{nm}-{nf}   cost £{res['cost']}m   "
          f"(£{round(100.0 - res['cost'], 1)}m left)   "
          f"XI projection {res['xi_proj']} pts over the modelled window")
    print(f"{'='*78}")
    print('  STARTING XI')
    for p in xi:
        c = ' (C)' if p['id'] == cap['id'] else (' (V)' if p['id'] == vice['id'] else '')
        print(f"    {p['pos']}  £{float(p['price']):>4}m  {p['name']:<16}{c:<4} "
              f"{p['team']:<4} {p['proj']:>5} pts  own {p['sel_pct']:>4}%")
    print('  BENCH')
    for p in bench:
        print(f"    {p['pos']}  £{float(p['price']):>4}m  {p['name']:<16}     "
              f"{p['team']:<4} {p['proj']:>5} pts  own {p['sel_pct']:>4}%")
    clubs = {}
    for p in s:
        clubs[p['team']] = clubs.get(p['team'], 0) + 1
    print('  club spread: ' + ', '.join(f'{k}×{v}' for k, v in
                                        sorted(clubs.items(), key=lambda x: -x[1]) if v > 1))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    players = load()
    by_name = {p['name']: p['id'] for p in players}
    results = []

    # 1. pure model optimum -- no constraints beyond the rules
    a = solve(players, label='OPTION A - "Model Optimum": highest projected XI, no constraints')
    results.append(a)

    # 2. template-safe: must include Haaland, the near-universal captain pick.
    #    Owning him removes the biggest source of rank volatility.
    b = solve(players, forced=[by_name['Haaland']],
              label='OPTION B - "Haaland Build": the 75%-owned captain, squad built around him')
    results.append(b)

    # 3. differential: cap ownership so the squad differs from the crowd
    c = solve(players, max_sel=25.0, min_sel=10.0,
              label='OPTION C - "Differential": nothing owned by more than 25% of managers')
    results.append(c)

    # 4. the conventional shape: keep 7 goalkeepers+defenders under £34.0m so the
    #    money goes into midfield and attack. Tests the standard FPL heuristic
    #    against the model's own preference.
    d = solve(players, max_def_spend=340,
              label='OPTION D - "Conventional": cheap defence, money spent up front')
    results.append(d)

    for r in results:
        if r:
            show(r)

    if args.json:
        json.dump([{k: (v if k != 'squad' else
                        [{kk: p[kk] for kk in
                          ('id', 'name', 'team', 'pos', 'price', 'proj',
                           'sel_pct', 'starting', 'mins_proj', 'news', 'note')}
                         for p in v])
                    for k, v in r.items()} for r in results if r],
                  open('data/squads.json', 'w'), indent=1)
        print('\nwrote data/squads.json')
