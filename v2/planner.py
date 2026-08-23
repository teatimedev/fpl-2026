"""
Multi-week transfer planner, from where you actually are.

A static squad is not how the game is played: you get one free transfer a week,
you can bank up to five, and extra transfers cost 4 points each. So the question
each week is not "what is the best swap" but "what is the best PATH" — and in
particular whether to use this week's transfer or hold it.

This is v1's plan.py re-pointed at v2: instead of choosing a GW1 squad from
scratch it starts from YOUR 15, YOUR bank and YOUR free-transfer count at the
next deadline, and solves the whole modelled window as one integer program:

  variables   x[p][gw]  player p is in the squad for gameweek gw
              y[p][gw]  ... and in the starting XI
              c[p][gw]  ... and captained
              in/out    transfers made before gameweek gw
  objective   XI points, captain counted twice, risk-weighted auto-sub cover,
              minus 4 per hit
  subject to  budget, 2/5/5/3, max 3 per club, a legal XI, and FPL's
              free-transfer accounting (one a week, bank up to five)

Assumptions worth knowing: prices are held static across the window, and FPL's
sell-price rule (you bank only half of any rise) is not modelled — the budget is
today's prices plus your bank. Both are minor over six weeks; both make the
plan slightly optimistic. wildcard_week models one wildcard gameweek:
unlimited free transfers that week, bank preserved (+1 accrues at the next
deadline as usual). Other chips are not modelled.

    from planner import plan
    res = plan(players, squad_ids, bank=0.5, ft=1, gw=7, horizon=12)
"""
import pulp

from squad_evaluator import evaluate_squad, modelled_bench_weights

SQUAD = {'GKP': 2, 'DEF': 5, 'MID': 5, 'FWD': 3}
XI_MIN = {'GKP': 1, 'DEF': 3, 'MID': 2, 'FWD': 1}
XI_MAX = {'GKP': 1, 'DEF': 5, 'MID': 5, 'FWD': 3}
HIT = 4.0
MAX_BANK = 5
POOL = 120


def _valid_incumbent(prob, tolerance=1e-5):
    """Whether PuLP currently holds a complete, integral, feasible solution."""
    for variable in prob.variables():
        value = variable.value()
        if value is None or abs(value - round(value)) > tolerance:
            return False
    try:
        constraints = getattr(prob, "_constraints", None)
        if constraints is None:
            public = prob.constraints
            constraints = public() if callable(public) else public.values()
        else:
            constraints = constraints.values()
        return all(constraint.valid(tolerance) for constraint in constraints)
    except TypeError:
        return False


def _pool(players, owned, gw, horizon):
    def rem(p):
        v = p['proj_by_gw']
        return sum(v[gw - 1:horizon]) if gw - 1 < len(v) else 0.0
    cands = [p for p in players.values() if p['status'] != 'u' and rem(p) > 0]
    keep = {p['id'] for p in sorted(cands, key=lambda p: -rem(p))[:POOL]}
    keep |= set(owned)
    return [players[i] for i in keep if i in players]


def plan(players, owned, bank, ft, gw, horizon, allow_hits=True,
         freeze_this_week=False, time_limit=30, wildcard_week=None):
    """Optimal transfer path from `owned` over GW gw..horizon.

    `ft` is the number of free transfers available at the coming deadline
    (before Gameweek 1 pass 15: everything is free). `freeze_this_week`
    forbids any transfer before the coming deadline — solve both and the
    difference is what using the transfer now is worth versus holding it.
    Set `wildcard_week` to a gameweek inside the window to model playing the
    wildcard chip that week: unlimited free transfers at no point cost, and
    per FPL's chip rules the free-transfer bank is neither spent nor gained
    that week (it rolls over plus one as usual into the following deadline).
    """
    pool = _pool(players, owned, gw, horizon)
    ids = [p['id'] for p in pool]
    P = {p['id']: p for p in pool}
    GW = list(range(gw, horizon + 1))
    if not GW:
        return None
    if wildcard_week is not None:
        if wildcard_week not in GW:
            raise ValueError(
                f'wildcard_week {wildcard_week} outside planned window '
                f'GW{gw}..{horizon}')
        if gw == 1:
            # GW1's "unlimited pre-season transfers, nothing carries" rule
            # and the wildcard accounting would both govern the same solve;
            # refuse rather than pick a silent precedence.
            raise ValueError('wildcard_week cannot be combined with a '
                             'pre-season window starting at GW1')
    pts = {(i, g): (P[i]['proj_by_gw'][g - 1] if g - 1 < len(P[i]['proj_by_gw']) else 0.0)
           for i in ids for g in GW}
    price = {i: int(round(P[i]['price'] * 10)) for i in ids}
    budget = sum(price[i] for i in owned if i in price) + int(round(bank * 10))
    seed = [P[i] for i in owned if i in P]
    bench_weights = {g: modelled_bench_weights(seed or pool, g) for g in GW}

    prob = pulp.LpProblem('fpl_path', pulp.LpMaximize)
    x = {(i, g): pulp.LpVariable(f'x{i}_{g}', cat='Binary') for i in ids for g in GW}
    y = {(i, g): pulp.LpVariable(f'y{i}_{g}', cat='Binary') for i in ids for g in GW}
    c = {(i, g): pulp.LpVariable(f'c{i}_{g}', cat='Binary') for i in ids for g in GW}
    tin = {(i, g): pulp.LpVariable(f'i{i}_{g}', cat='Binary') for i in ids for g in GW}
    tout = {(i, g): pulp.LpVariable(f'o{i}_{g}', cat='Binary') for i in ids for g in GW}
    hits = {g: pulp.LpVariable(f'h{g}', lowBound=0, cat='Integer') for g in GW}
    # free transfers available at each deadline; the first is given
    ftv = {g: pulp.LpVariable(f'f{g}', lowBound=0, upBound=max(MAX_BANK, ft), cat='Integer')
           for g in GW}

    def objective(weights):
        return (pulp.lpSum(y[(i, g)] * pts[(i, g)] for i in ids for g in GW)
                + pulp.lpSum(c[(i, g)] * pts[(i, g)] for i in ids for g in GW)
                + pulp.lpSum(
                    (x[(i, g)] - y[(i, g)]) * pts[(i, g)]
                    * (weights[g]['GKP'] if P[i]['pos'] == 'GKP'
                       else weights[g]['outfield'])
                    for i in ids for g in GW
                )
                - HIT * pulp.lpSum(hits[g] for g in GW))

    prob += objective(bench_weights)

    for g in GW:
        prob += pulp.lpSum(x[(i, g)] for i in ids) == 15
        for pos, n in SQUAD.items():
            prob += pulp.lpSum(x[(i, g)] for i in ids if P[i]['pos'] == pos) == n
        prob += pulp.lpSum(x[(i, g)] * price[i] for i in ids) <= budget
        for club in {p['team'] for p in pool}:
            prob += pulp.lpSum(x[(i, g)] for i in ids if P[i]['team'] == club) <= 3
        prob += pulp.lpSum(y[(i, g)] for i in ids) == 11
        for pos in SQUAD:
            n = pulp.lpSum(y[(i, g)] for i in ids if P[i]['pos'] == pos)
            prob += n >= XI_MIN[pos]
            prob += n <= XI_MAX[pos]
        prob += pulp.lpSum(c[(i, g)] for i in ids) == 1
        for i in ids:
            prob += y[(i, g)] <= x[(i, g)]
            prob += c[(i, g)] <= y[(i, g)]

    # transfer linking: the week before the first modelled week is the squad
    # you own today
    for k, g in enumerate(GW):
        for i in ids:
            prev_x = (1 if i in owned else 0) if k == 0 else x[(i, GW[k - 1])]
            prob += x[(i, g)] - prev_x == tin[(i, g)] - tout[(i, g)]
            prob += tin[(i, g)] + tout[(i, g)] <= 1
        n_out = pulp.lpSum(tout[(i, g)] for i in ids)
        if k == 0:
            prob += ftv[g] == ft
        wc = g == wildcard_week
        # Wildcard week: unlimited free transfers make the hit floor moot.
        # The hits variable stays (output code indexes it); the objective's
        # -HIT per hit pins it to 0 without an explicit constraint.
        if not wc:
            prob += hits[g] >= n_out - ftv[g]
        if not allow_hits:
            prob += hits[g] == 0
        if freeze_this_week and k == 0:
            prob += n_out == 0
        if k + 1 < len(GW):
            nxt = GW[k + 1]
            if wc:
                # Wildcard week (FPL chip rules): FTs are neither spent nor
                # gained — unlimited outs must not drain the bank, so the
                # standard rollover inequality is dropped for this single
                # transition and only the preserved bank (+1, capped) carries.
                prob += ftv[nxt] <= ftv[g] + 1
            else:
                # what is left rolls over, plus one, capped at five
                prob += ftv[nxt] <= ftv[g] - n_out + hits[g] + 1
            prob += ftv[nxt] <= MAX_BANK
            if g == 1:
                # pre-season transfers are unlimited but nothing carries over:
                # everyone starts Gameweek 2 with exactly one
                prob += ftv[nxt] <= 1

    # First find a legal path, then refit the linear bench proxy to the squads
    # that path actually selected. One refit captures the material difference
    # without turning the normal weekly command into an open-ended loop.
    prob.solve(pulp.HiGHS(msg=False, timeLimit=time_limit))
    if (pulp.LpStatus[prob.status] not in ('Optimal', 'Not Solved')
            or not _valid_incumbent(prob)):
        return None
    first_incumbent = {variable.name: variable.value() for variable in prob.variables()}
    selected = tuple(
        tuple(sorted(i for i in ids if (x[(i, g)].value() or 0) > 0.5))
        for g in GW
    )
    bench_weights = {
        g: modelled_bench_weights([P[i] for i in selected[k]], g)
        for k, g in enumerate(GW)
    }
    prob.setObjective(objective(bench_weights))
    prob.solve(pulp.HiGHS(msg=False, timeLimit=time_limit))
    if (pulp.LpStatus[prob.status] not in ('Optimal', 'Not Solved')
            or not _valid_incumbent(prob)):
        # A timeout may leave no second incumbent. The first pass was checked
        # in full, so retain that feasible path instead of reading partial
        # variable values as a squad.
        for variable in prob.variables():
            variable.varValue = first_incumbent[variable.name]

    out = {'weeks': [], 'total': 0.0, 'hits': 0, 'gw': gw, 'horizon': horizon}
    for g in GW:
        squad = [i for i in ids if (x[(i, g)].value() or 0) > 0.5]
        evaluation = evaluate_squad([P[i] for i in squad], g, g)
        lineup = evaluation.weeks[0].lineup
        xi = [p['id'] for p in lineup.xi]
        cap = lineup.captain['id'] if lineup.captain else None
        wk = evaluation.total
        h = int(round(hits[g].value() or 0))
        out['total'] += wk - HIT * h
        out['hits'] += h
        out['weeks'].append({
            'gw': g, 'pts': round(wk, 1), 'hits': h,
            'squad': squad, 'xi': xi, 'captain': cap,
            'autosub': round(evaluation.autosub_points, 1),
            'in': [i for i in ids if (tin[(i, g)].value() or 0) > 0.5],
            'out': [i for i in ids if (tout[(i, g)].value() or 0) > 0.5],
            'ft': int(round(ftv[g].value() or 0)),
            'cost': sum(price[i] for i in squad) / 10,
        })
    out['total'] = round(out['total'], 1)
    return out


def describe(res, players):
    """Human lines for the digest."""
    if not res:
        return ['Planner could not find a solution in time.']
    L = []
    for w in res['weeks']:
        moves = ''
        if w['in']:
            paired = []
            for pos in ('GKP', 'DEF', 'MID', 'FWD'):
                incoming = [i for i in w['in'] if players[i]['pos'] == pos]
                outgoing = [o for o in w['out'] if players[o]['pos'] == pos]
                paired.extend(zip(outgoing, incoming))
            pairs = ', '.join(f"{players[o]['name']} → {players[i]['name']}"
                              for o, i in paired)
            moves = f'  {pairs}' + (f'  (−{w["hits"] * 4} hit)' if w['hits'] else '')
        capn = players[w['captain']]['name'] if w['captain'] in players else '—'
        L.append(f"- **GW{w['gw']}** {w['pts']:.1f} pts, C {capn}, "
                 f"{w['ft']} FT{moves or '  hold'}")
    return L
