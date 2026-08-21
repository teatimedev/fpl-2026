"""
FPL 2026/27 squad optimiser.

Solves for a 15-player squad under the real game constraints:
  - £100.0m budget
  - 2 GKP, 5 DEF, 5 MID, 3 FWD
  - maximum 3 players per club
  - a legal starting XI (1 GKP, 3-5 DEF, 2-5 MID, 1-3 FWD)

The objective maximises the projected points of the starting XI plus a bench
activation rate derived from the modelled XI's actual non-appearance
probabilities. A reserve is therefore worth more when the lineup is fragile and
zero when nobody is expected to miss out; there is no fixed bench percentage.
"""
import csv, json, argparse
import pulp

from v2.squad_evaluator import evaluate_squad, modelled_bench_weights

BUDGET = 1000        # tenths of a million
SQUAD = {1: 2, 2: 5, 3: 5, 4: 3}
XI_MIN = {1: 1, 2: 3, 3: 2, 4: 1}
XI_MAX = {1: 1, 2: 5, 3: 5, 4: 3}
POS_ID = {'GKP': 1, 'DEF': 2, 'MID': 3, 'FWD': 4}
META = json.load(open('v2/projections_v2.json'))
START_GW = META.get('start_gw', 1)
HORIZON = META.get('horizon', START_GW + 5)


def load():
    rows = []
    for r in csv.DictReader(open('data/projections.csv')):
        r['price_t'] = int(round(float(r['price']) * 10))
        r['proj'] = float(r['proj_6gw'])
        r['pos_id'] = POS_ID[r['pos']]
        r['mins_proj'] = int(r['mins_proj'])
        r['sel_pct'] = float(r['sel_pct'])
        r['proj_by_gw'] = json.loads(r['proj_by_gw'])
        r['start_by_gw'] = json.loads(r.get('start_by_gw') or '[]')
        r['play_by_gw'] = json.loads(r.get('play_by_gw') or '[]')
        rows.append(r)
    return rows


def _refine_exact_squad(squad, pool, *, forced=(), max_per_club=3,
                        budget=BUDGET, min_sel=None, max_sel=None,
                        exclude_squads=(), max_def_spend=None,
                        min_att_spend=None, max_passes=8):
    """Climb legal same-position swaps using the full shared evaluator.

    The MILP's bench term must be linear, so it cannot express every bench-order
    interaction. This bounded refinement rejects any selected squad with an
    immediately better legal one-player neighbour under the exact score.
    """
    pool_ids = {p['id'] for p in pool}
    forced = set(forced)
    previous = [set(prev) & pool_ids for prev in exclude_squads]
    cache = {}

    def score(candidate):
        key = tuple(sorted(p['id'] for p in candidate))
        if key not in cache:
            cache[key] = evaluate_squad(candidate, START_GW, HORIZON)
        return cache[key]

    def legal(candidate):
        ids = {p['id'] for p in candidate}
        if not forced <= ids:
            return False
        if sum(p['price_t'] for p in candidate) > budget:
            return False
        clubs = {}
        for p in candidate:
            clubs[p['team']] = clubs.get(p['team'], 0) + 1
            if clubs[p['team']] > max_per_club:
                return False
        if max_sel is not None and any(p['sel_pct'] > max_sel for p in candidate):
            return False
        if min_sel is not None and sum(p['sel_pct'] >= min_sel for p in candidate) < 6:
            return False
        defensive = sum(p['price_t'] for p in candidate if p['pos_id'] in (1, 2))
        attacking = sum(p['price_t'] for p in candidate if p['pos_id'] in (3, 4))
        if max_def_spend is not None and defensive > max_def_spend:
            return False
        if min_att_spend is not None and attacking < min_att_spend:
            return False
        return all(len(ids & prev) <= len(prev) - 5 for prev in previous)

    current = list(squad)
    initial = score(current).total
    for _ in range(max_passes):
        current_ids = {p['id'] for p in current}
        best, best_value = None, score(current).total
        for outgoing in current:
            if outgoing['id'] in forced:
                continue
            for incoming in pool:
                if (incoming['id'] in current_ids or incoming['pos'] != outgoing['pos']
                        or incoming['status'] == 'u'):
                    continue
                candidate = [incoming if p['id'] == outgoing['id'] else p
                             for p in current]
                if not legal(candidate):
                    continue
                value = score(candidate).total
                if value > best_value + 1e-6:
                    best, best_value = candidate, value
        if best is None:
            break
        current = best
    evaluation = score(current)
    return current, evaluation, evaluation.total - initial


def solve(players, banned=(), forced=(), max_per_club=3, budget=BUDGET,
          min_sel=None, max_sel=None, exclude_squads=(), label='',
          max_def_spend=None, min_att_spend=None):
    """Return one refined MILP seed. ``exclude_squads`` adds diversity."""
    pool = [p for p in players if p['id'] not in banned
            and p['status'] != 'u' and p['proj'] > 0]

    prob = pulp.LpProblem('fpl', pulp.LpMaximize)
    pick = {p['id']: pulp.LpVariable(f"p{p['id']}", cat='Binary') for p in pool}
    gameweeks = list(range(START_GW, HORIZON + 1))
    start = {(p['id'], gw): pulp.LpVariable(f"s{p['id']}_{gw}", cat='Binary')
             for p in pool for gw in gameweeks}
    captain = {(p['id'], gw): pulp.LpVariable(f"c{p['id']}_{gw}", cat='Binary')
               for p in pool for gw in gameweeks}

    def weights_for(source):
        return {gw: modelled_bench_weights(source, gw)
                for gw in gameweeks}

    def objective(weights):
        return pulp.lpSum(
            start[(p['id'], gw)] * p['proj_by_gw'][gw - 1]
            + captain[(p['id'], gw)] * p['proj_by_gw'][gw - 1]
            + (pick[p['id']] - start[(p['id'], gw)])
            * p['proj_by_gw'][gw - 1]
                * (weights[gw]['GKP'] if p['pos'] == 'GKP'
                   else weights[gw]['outfield'])
            for p in pool for gw in gameweeks
        )

    weights = weights_for(pool)
    prob += objective(weights)

    # squad shape
    for et, n in SQUAD.items():
        prob += pulp.lpSum(pick[p['id']] for p in pool if p['pos_id'] == et) == n
    prob += pulp.lpSum(pick[p['id']] * p['price_t'] for p in pool) <= budget
    prob += pulp.lpSum(pick[p['id']] for p in pool) == 15

    # Managers reselect the XI every deadline; model each gameweek separately.
    for gw in gameweeks:
        prob += pulp.lpSum(start[(p['id'], gw)] for p in pool) == 11
        prob += pulp.lpSum(captain[(p['id'], gw)] for p in pool) == 1
        for et in SQUAD:
            n = pulp.lpSum(start[(p['id'], gw)] for p in pool if p['pos_id'] == et)
            prob += n >= XI_MIN[et]
            prob += n <= XI_MAX[et]
        for p in pool:
            prob += start[(p['id'], gw)] <= pick[p['id']]
            prob += captain[(p['id'], gw)] <= start[(p['id'], gw)]

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
    # The first pass uses the whole candidate pool. Refit the linear DNP proxy
    # to the selected 15 and resolve until the squad/weights stabilise.
    previous = None
    for _ in range(3):
        prob.solve(pulp.HiGHS(msg=False))
        if pulp.LpStatus[prob.status] != 'Optimal':
            return None
        selected = tuple(sorted(p['id'] for p in pool
                                if pick[p['id']].value() > 0.5))
        if selected == previous:
            break
        previous = selected
        selected_players = [p for p in pool if p['id'] in selected]
        weights = weights_for(selected_players)
        prob.setObjective(objective(weights))
    else:
        prob.solve(pulp.HiGHS(msg=False))

    # copy each player: the pool dicts are shared across solves, so mutating them
    # in place would let a later solution overwrite an earlier one's XI.
    squad = [dict(p) for p in pool if pick[p['id']].value() > 0.5]
    squad, evaluation, refinement_gain = _refine_exact_squad(
        squad, pool, forced=forced, max_per_club=max_per_club, budget=budget,
        min_sel=min_sel, max_sel=max_sel, exclude_squads=exclude_squads,
        max_def_spend=max_def_spend, min_att_spend=min_att_spend,
    )
    weights = weights_for(squad)
    current_xi = {p['id'] for p in evaluation.weeks[0].lineup.xi}
    squad = [dict(p, starting=(p['id'] in current_xi)) for p in squad]
    first_lineup = evaluation.weeks[0].lineup
    return {'label': label, 'squad': squad,
            'cost': sum(p['price_t'] for p in squad) / 10,
            'xi_proj': round(evaluation.xi_points, 1),
            'squad_proj': round(sum(p['proj'] for p in squad), 1),
            'expected_total': round(evaluation.total, 1),
            'autosub_proj': round(evaluation.autosub_points, 1),
            'exact_refinement_gain': round(refinement_gain, 2),
            'captain': first_lineup.captain['id'] if first_lineup.captain else None,
            'vice': first_lineup.vice['id'] if first_lineup.vice else None,
            'bench_weights': {
                pos: round(sum(w[pos] for w in weights.values()) / len(weights), 3)
                for pos in ('GKP', 'outfield')
            }}


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
    cap = next((p for p in xi if p['id'] == res['captain']), xi[0])
    vice = next((p for p in xi if p['id'] == res['vice']), xi[1])

    print(f"\n{'='*78}")
    print(f"  {res['label']}")
    print(f"  formation {nd}-{nm}-{nf}   cost £{res['cost']}m   "
          f"(£{round(100.0 - res['cost'], 1)}m left)   "
          f"XI projection {res['xi_proj']} pts over the modelled window")
    print(f"  risk-adjusted squad value {res['expected_total']} pts "
          f"(modelled auto-subs {res['autosub_proj']} pts; "
          f"bench activation GKP {res['bench_weights']['GKP']:.1%}, "
          f"outfield {res['bench_weights']['outfield']:.1%})")
    if res['exact_refinement_gain'] > 0.01:
        print(f"  exact evaluator refinement +{res['exact_refinement_gain']:.1f} pts "
              "after the linear solve")
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

    # 1. unrestricted linear seed -- exact refinement below can move away from it
    a = solve(players, label='"Unrestricted Seed": no forced players')
    results.append(a)

    # 2. template-safe: must include Haaland, the near-universal captain pick.
    #    Owning him removes the biggest source of rank volatility.
    b = solve(players, forced=[by_name['Haaland']],
              label='"Haaland Build": premium captain included')
    results.append(b)

    # 3. differential: cap ownership so the squad differs from the crowd
    c = solve(players, max_sel=25.0, min_sel=10.0,
              label='"Differential": nothing owned by more than 25% of managers')
    results.append(c)

    # 4. the conventional shape: keep 7 goalkeepers+defenders under £34.0m so the
    #    money goes into midfield and attack. Tests the standard FPL heuristic
    #    against the model's own preference.
    d = solve(players, max_def_spend=340,
              label='"Conventional": cheap defence, money spent up front')
    results.append(d)

    # The nonlinear exact score can rank a constrained seed above the
    # unrestricted linear seed. Promote the best unique result instead of
    # calling the first MILP output an optimum it has not proved.
    available = [r for r in results if r]
    best = max(available, key=lambda r: r['expected_total'])
    ordered = [best] + [r for r in available if r is not best]
    for index, result in enumerate(ordered):
        suffix = result['label']
        if index == 0:
            suffix = '"Best Found": highest exact score across the searched builds'
        result['label'] = f"OPTION {'ABCD'[index]} - {suffix}"
    results = ordered

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
