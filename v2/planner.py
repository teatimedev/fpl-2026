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
              minus 4 per hit — each week weighted by decay**(weeks ahead) —
              plus a terminal value (see Valuation)
  subject to  budget, 2/5/5/3, max 3 per club, a legal XI, and FPL's
              free-transfer accounting (one a week, bank up to five)

Terminal value (Valuation, opt-in; weekly.py passes production_valuation()):
without it, everything the squad is worth AFTER the window counts for zero —
banked free transfers, money in the bank and the horizon squad's later points.
That makes the solver spend transfers in the last week for one week of points
and makes a player with no route to minutes all season (a loanee) nearly free
to keep, because over six weeks he only costs bench-cover. The valuation adds

  decay      a per-week weight decay**k on week gw+k (forecast error and the
             planner's own later moves grow with lead time)
  ft_values  the value of each free transfer banked at the horizon beyond the
             one every manager gets (2nd, 3rd, 4th, 5th: concave)
  bank_value points per £1m left in the bank at the horizon
  tail       the horizon squad's decayed points over `tail_weeks` more weeks,
             from the season-long projection (or the player's late-window
             rate when no season projection is supplied), valued as one
             aggregated block with its own XI, captain and bench weight

All of it stays linear: the model is still one MILP.

Assumptions worth knowing: prices are held static across the window. Supply
sell_prices for initially owned players to account for the profit lost on
their first sale; later repurchases cost the full current price. Without
sell_prices, the legacy current-price budget is used. wildcard_week models one wildcard gameweek:
unlimited free transfers that week, with the free-transfer bank preserved
unchanged at the next deadline. Other chips are not modelled.

    from planner import plan
    res = plan(players, squad_ids, bank=0.5, ft=1, gw=7, horizon=12)
"""
import math
from dataclasses import dataclass, field

import pulp

from squad_evaluator import evaluate_squad, evaluate_week, modelled_bench_weights

SQUAD = {'GKP': 2, 'DEF': 5, 'MID': 5, 'FWD': 3}
XI_MIN = {'GKP': 1, 'DEF': 3, 'MID': 2, 'FWD': 1}
XI_MAX = {'GKP': 1, 'DEF': 5, 'MID': 5, 'FWD': 3}
HIT = 4.0
MAX_BANK = 5
POOL = 120
LAST_GW = 38

# --- production valuation (research/planner-phase4-2026-09-23.md) ----------
# Decay and the free-transfer/bank values are the defaults of the open-source
# FPL-Optimization-Tools solver (sertalpbilal, data/comprehensive_settings.json:
# decay_base 0.9, ft_value_list {2: 2, 3: 1.6, 4: 1.3, 5: 1.1}, itb_value
# 0.08). 0.9 also matches this model's own lead-time bias in 2026/27
# (actual/forecast about 0.94, 0.86, 0.84 at leads 0, 1, 2 weeks).
DECAY = 0.9
# Value of the 2nd..5th free transfer banked at the horizon (concave).
FT_VALUES = (2.0, 1.6, 1.3, 1.1)
# Points per £1m unspent at the horizon: a tie-breaker, not a target.
BANK_VALUE = 0.08
TAIL_WEEKS = 6


@dataclass(frozen=True)
class Valuation:
    decay: float = 1.0
    ft_values: tuple = ()
    bank_value: float = 0.0
    tail_weeks: int = 0
    # pid -> player-like dict whose proj_by_gw/play_by_gw run to LAST_GW
    tail: dict | None = field(default=None, compare=False, hash=False)

    def weight(self, gw, g):
        return self.decay ** (g - gw)

    def describe(self):
        return dict(decay=self.decay, ft_values=list(self.ft_values),
                    bank_value=self.bank_value, tail_weeks=self.tail_weeks,
                    tail_source='season projection' if self.tail else
                    ('late-window rate' if self.tail_weeks else None))


LEGACY = Valuation()


def production_valuation(tail=None, decay=DECAY, ft_values=FT_VALUES,
                         bank_value=BANK_VALUE, tail_weeks=TAIL_WEEKS):
    return Valuation(decay=decay, ft_values=tuple(ft_values), bank_value=bank_value,
                     tail_weeks=tail_weeks, tail=tail)


def season_tail(season_players):
    """Tail dicts from projections_season.json players (by_gw -> proj_by_gw)."""
    return {pid: dict(id=pid, pos=p['pos'], team=p['team'], name=p.get('name'),
                      status=p.get('status', 'a'), proj_by_gw=list(p['by_gw']),
                      play_by_gw=list(p.get('play_by_gw') or []))
            for pid, p in season_players.items()}


def transfer_ledger(ft, moves, gw, wildcard=False):
    """Exact available transfers, hit count and rollover for a selected path."""
    if gw == 1:
        return dict(ft=ft, hits=0, ft_next=1, ft_lost=0)
    if wildcard:
        return dict(ft=ft, hits=0, ft_next=ft, ft_lost=0)
    remaining = max(0, ft - moves)
    return dict(ft=ft, hits=max(0, moves - ft),
                ft_next=min(MAX_BANK, remaining + 1),
                ft_lost=max(0, remaining + 1 - MAX_BANK))


def ft_terminal_value(ft_end, ft_values):
    """Concave value of the transfers banked at the horizon (beyond the first)."""
    return sum(ft_values[:max(0, min(len(ft_values), int(ft_end) - 1))])


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


def _solver_diagnostics(prob):
    """Expose the actual HiGHS termination/gap, including feasible timeouts.

    PuLP's broad status alone is insufficient for small act-versus-hold
    differences. Bounds refer to the linear bench approximation, not the
    exact squad evaluation returned in total.
    """
    model = getattr(prob, 'solverModel', None)
    if model is None:
        return {'status': pulp.LpStatus[prob.status]}
    info = model.getInfo()
    def finite(value):
        return float(value) if value is not None and math.isfinite(value) else None
    # HiGHS bounds exclude the objective's constant term (the bank value's
    # budget offset); add it back so the bound is comparable with objective.
    constant = getattr(prob.objective, 'constant', 0.0) or 0.0
    bound = finite(-info.mip_dual_bound)
    return dict(status=str(model.getModelStatus()).split('.')[-1],
                mip_gap=finite(info.mip_gap),
                objective=finite(pulp.value(prob.objective)),
                upper_bound=bound + constant if bound is not None else None)


def _window_points(p, gw, horizon):
    v = p['proj_by_gw']
    return sum(v[gw - 1:horizon]) if gw - 1 < len(v) else 0.0


def _pool(players, owned, gw, horizon, size=POOL):
    cands = [p for p in players.values()
             if p['status'] != 'u' and _window_points(p, gw, horizon) > 0]
    keep = {p['id'] for p in sorted(cands, key=lambda p: -_window_points(p, gw, horizon))[:size]}
    keep |= set(owned)
    return [players[i] for i in keep if i in players]


def tail_weeks(valuation, horizon):
    if not valuation.tail_weeks:
        return []
    return list(range(horizon + 1, min(LAST_GW, horizon + valuation.tail_weeks) + 1))


def tail_player(player, valuation, gw, horizon):
    """A player-like dict whose proj_by_gw covers the tail weeks.

    Uses the season projection when supplied; otherwise the player's mean
    projection over the last two modelled weeks, held flat (a simple
    rate x remaining fixtures proxy, without fixture strength).
    """
    season = (valuation.tail or {}).get(player['id'])
    if season is not None:
        return {**player, 'proj_by_gw': season['proj_by_gw'],
                'play_by_gw': season.get('play_by_gw') or player.get('play_by_gw')}
    v = player['proj_by_gw']
    late = [v[g - 1] for g in (horizon - 1, horizon) if gw <= g and 0 <= g - 1 < len(v)]
    rate = sum(late) / len(late) if late else 0.0
    play = player.get('play_by_gw') or []
    late_play = play[horizon - 1] if 0 <= horizon - 1 < len(play) else None
    out = {**player, 'proj_by_gw': [rate] * LAST_GW}
    if late_play is not None:
        out['play_by_gw'] = [late_play] * LAST_GW
    return out


class PathModel:
    """The path MILP built once, re-solvable with new points or a fixed first week.

    Building the constraints is most of the Python cost of a solve, so the
    act-versus-hold sampler (act_or_hold.py) builds one model and swaps only
    the objective and the first-week bounds between solves.
    """

    def __init__(self, players, owned, bank, ft, gw, horizon, allow_hits=True,
                 wildcard_week=None, sell_prices=None, valuation=None,
                 pool_size=POOL, extra_ids=()):
        self.valuation = valuation or LEGACY
        self.players, self.owned, self.ft = players, list(owned), ft
        self.gw, self.horizon, self.wildcard_week = gw, horizon, wildcard_week
        pool = _pool(players, list(set(owned) | set(extra_ids)), gw, horizon, pool_size)
        self.ids = ids = [p['id'] for p in pool]
        self.P = P = {p['id']: p for p in pool}
        self.GW = GW = list(range(gw, horizon + 1))
        self.TAIL = tail_weeks(self.valuation, horizon)
        if not GW:
            raise ValueError('empty planning window')
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
        self.price = price = {i: int(round(P[i]['price'] * 10)) for i in ids}
        self.budget = budget = sum(price[i] for i in owned if i in price) + int(round(bank * 10))
        self.discounts = discounts = {}
        for i, sell in (sell_prices or {}).items():
            if i not in owned or i not in price:
                raise ValueError('sell_prices must refer to owned players in the pool')
            sale = int(round(sell * 10))
            if not 0 <= sale <= price[i]:
                raise ValueError('selling price must be between zero and current price')
            if sale < price[i]:
                discounts[i] = price[i] - sale
        seed = [P[i] for i in owned if i in P]
        self.bench_weights = {g: modelled_bench_weights(seed or pool, g) for g in GW}

        self.prob = prob = pulp.LpProblem('fpl_path', pulp.LpMaximize)
        self.x = x = {(i, g): pulp.LpVariable(f'x{i}_{g}', cat='Binary') for i in ids for g in GW}
        self.y = y = {(i, g): pulp.LpVariable(f'y{i}_{g}', cat='Binary') for i in ids for g in GW}
        self.c = c = {(i, g): pulp.LpVariable(f'c{i}_{g}', cat='Binary') for i in ids for g in GW}
        self.tin = tin = {(i, g): pulp.LpVariable(f'i{i}_{g}', cat='Binary') for i in ids for g in GW}
        self.tout = tout = {(i, g): pulp.LpVariable(f'o{i}_{g}', cat='Binary') for i in ids for g in GW}
        # Whether the original acquisition lot has been retained continuously.
        # Once sold it stays zero, including after a later full-price repurchase.
        retained = {(i, g): pulp.LpVariable(f'r{i}_{g}', cat='Binary')
                    for i in discounts for g in GW}
        self.retained = retained
        self.hits = hits = {g: pulp.LpVariable(f'h{g}', lowBound=0, cat='Integer') for g in GW}
        # free transfers available at each deadline; the first is given
        self.ftv = ftv = {g: pulp.LpVariable(f'f{g}', lowBound=0, upBound=max(MAX_BANK, ft), cat='Integer')
                          for g in GW}

        for g in GW:
            prob += pulp.lpSum(x[(i, g)] for i in ids) == 15
            for pos, n in SQUAD.items():
                prob += pulp.lpSum(x[(i, g)] for i in ids if P[i]['pos'] == pos) == n
            prob += (pulp.lpSum(x[(i, g)] * price[i] for i in ids)
                     + pulp.lpSum(discounts[i] * (1-retained[(i, g)]) for i in discounts)
                     <= budget)
            for club in {p['team'] for p in pool}:
                prob += pulp.lpSum(x[(i, g)] for i in ids if P[i]['team'] == club) <= 3
            self._lineup(y, c, x, g)

        # transfer linking: the week before the first modelled week is the squad
        # you own today
        for k, g in enumerate(GW):
            for i in ids:
                prev_x = (1 if i in owned else 0) if k == 0 else x[(i, GW[k - 1])]
                prob += x[(i, g)] - prev_x == tin[(i, g)] - tout[(i, g)]
                prob += tin[(i, g)] + tout[(i, g)] <= 1
            for i in discounts:
                prev = 1 if k == 0 else retained[(i, GW[k - 1])]
                prob += retained[(i, g)] <= prev
                prob += retained[(i, g)] <= x[(i, g)]
                prob += retained[(i, g)] >= prev - tout[(i, g)]
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
            if k + 1 < len(GW):
                nxt = GW[k + 1]
                if wc:
                    # Wildcard week (FPL chip rules): FTs are neither spent nor
                    # gained — unlimited outs must not drain the bank, so the
                    # standard rollover inequality is dropped for this single
                    # transition and only the unchanged bank carries.
                    prob += ftv[nxt] <= ftv[g]
                else:
                    # what is left rolls over, plus one, capped at five
                    prob += ftv[nxt] <= ftv[g] - n_out + hits[g] + 1
                prob += ftv[nxt] <= MAX_BANK
                if g == 1:
                    # pre-season transfers are unlimited but nothing carries over:
                    # everyone starts Gameweek 2 with exactly one
                    prob += ftv[nxt] <= 1

        # ---- terminal value: banked FTs, bank money, the horizon squad's tail
        H = GW[-1]
        v = self.valuation
        self.ft_extra = [pulp.LpVariable(f'fe{k}', lowBound=0, upBound=1)
                         for k in range(len(v.ft_values))]
        if self.ft_extra:
            n_out = pulp.lpSum(tout[(i, H)] for i in ids)
            if H == 1:
                end_cap = 1
            elif H == wildcard_week:
                end_cap = ftv[H]
            else:
                end_cap = ftv[H] - n_out + hits[H] + 1
            prob += 1 + pulp.lpSum(self.ft_extra) <= end_cap
        self.bank_end = (budget - pulp.lpSum(x[(i, H)] * price[i] for i in ids)
                         - pulp.lpSum(discounts[i] * (1 - retained[(i, H)]) for i in discounts))
        if self.TAIL:
            self.yT = {i: pulp.LpVariable(f'yT{i}', cat='Binary') for i in ids}
            self.cT = {i: pulp.LpVariable(f'cT{i}', cat='Binary') for i in ids}
            prob += pulp.lpSum(self.yT[i] for i in ids) == 11
            for pos in SQUAD:
                n = pulp.lpSum(self.yT[i] for i in ids if P[i]['pos'] == pos)
                prob += n >= XI_MIN[pos]
                prob += n <= XI_MAX[pos]
            prob += pulp.lpSum(self.cT[i] for i in ids) == 1
            for i in ids:
                prob += self.yT[i] <= x[(i, H)]
                prob += self.cT[i] <= self.yT[i]
            self.tail_players = {i: tail_player(P[i], v, gw, horizon) for i in ids}
        else:
            self.yT = self.cT = None
            self.tail_players = {}

    def _lineup(self, y, c, x, g):
        prob, ids, P = self.prob, self.ids, self.P
        prob += pulp.lpSum(y[(i, g)] for i in ids) == 11
        for pos in SQUAD:
            n = pulp.lpSum(y[(i, g)] for i in ids if P[i]['pos'] == pos)
            prob += n >= XI_MIN[pos]
            prob += n <= XI_MAX[pos]
        prob += pulp.lpSum(c[(i, g)] for i in ids) == 1
        for i in ids:
            prob += y[(i, g)] <= x[(i, g)]
            prob += c[(i, g)] <= y[(i, g)]

    # ------------------------------------------------------------ objective
    def points(self, players=None):
        """(i, g) -> points and i -> decayed tail total, from `players`."""
        src = players or self.P
        pts = {}
        for i in self.ids:
            v = src[i]['proj_by_gw']
            for g in self.GW:
                pts[(i, g)] = v[g - 1] if g - 1 < len(v) else 0.0
        tail = {}
        if self.TAIL:
            tp = self.tail_source(src)
            for i in self.ids:
                v = tp[i]['proj_by_gw']
                tail[i] = sum(self.valuation.weight(self.gw, g) * (v[g - 1] if g - 1 < len(v) else 0.0)
                              for g in self.TAIL)
        return pts, tail

    def tail_source(self, players=None):
        if players is None or players is self.P:
            return self.tail_players
        # perturbed copies carry their own tail projection (see act_or_hold)
        return {i: players[i].get('_tail') or self.tail_players[i] for i in self.ids}

    def objective(self, pts, tail, bench_weights):
        x, y, c, ids, P, GW = self.x, self.y, self.c, self.ids, self.P, self.GW
        w = {g: self.valuation.weight(self.gw, g) for g in GW}
        expr = (pulp.lpSum(w[g] * (y[(i, g)] + c[(i, g)]) * pts[(i, g)] for i in ids for g in GW)
                + pulp.lpSum(
                    w[g] * (x[(i, g)] - y[(i, g)]) * pts[(i, g)]
                    * (bench_weights[g]['GKP'] if P[i]['pos'] == 'GKP'
                       else bench_weights[g]['outfield'])
                    for i in ids for g in GW)
                - HIT * pulp.lpSum(w[g] * self.hits[g] for g in GW))
        v = self.valuation
        # Banked transfers and money are options, not forecasts of a week's
        # points, so (as in the reference solver) they are not decayed.
        if self.ft_extra:
            expr += pulp.lpSum(val * e for val, e in zip(v.ft_values, self.ft_extra))
        if v.bank_value:
            expr += v.bank_value / 10 * self.bank_end
        if self.TAIL:
            bw = bench_weights[GW[-1]]
            H = GW[-1]
            expr += pulp.lpSum((self.yT[i] + self.cT[i]) * tail[i]
                               + (x[(i, H)] - self.yT[i]) * tail[i]
                               * (bw['GKP'] if P[i]['pos'] == 'GKP' else bw['outfield'])
                               for i in ids)
        return expr

    # ---------------------------------------------------------------- solve
    def _fix_first(self, squad):
        g = self.gw
        for i in self.ids:
            var = self.x[(i, g)]
            if squad is None:
                var.lowBound, var.upBound = 0, 1
            else:
                on = int(i in squad)
                var.lowBound, var.upBound = on, on

    def _run(self, time_limit, gap_abs):
        kwargs = dict(msg=False, timeLimit=time_limit)
        if gap_abs is not None:
            kwargs['gapAbs'] = gap_abs
        self.prob.solve(pulp.HiGHS(**kwargs))
        return (pulp.LpStatus[self.prob.status] in ('Optimal', 'Not Solved')
                and _valid_incumbent(self.prob))

    def solve(self, first_week_squad=None, players=None, time_limit=30, refit=True,
              gap_abs=None, bench_weights=None):
        """Best path, optionally with this week's squad fixed; None if infeasible.

        `players` (id -> player) replaces the projections used for the
        objective and the exact rescoring (the sampler's perturbed worlds).
        """
        if first_week_squad is not None:
            if len(set(first_week_squad)) != 15 or any(i not in self.P for i in first_week_squad):
                raise ValueError('first_week_squad must contain 15 distinct pool players')
        src = players or self.P
        pts, tail = self.points(src)
        weights = bench_weights or self.bench_weights
        self._fix_first(first_week_squad)
        try:
            self.prob.setObjective(self.objective(pts, tail, weights))
            if not self._run(time_limit, gap_abs):
                return None
            diagnostics = _solver_diagnostics(self.prob)
            if refit:
                # First find a legal path, then refit the linear bench proxy to
                # the squads that path actually selected. One refit captures the
                # material difference without an open-ended loop.
                first_incumbent = {v.name: v.value() for v in self.prob.variables()}
                selected = [[self.P[i] for i in self.ids if (self.x[(i, g)].value() or 0) > 0.5]
                            for g in self.GW]
                weights = {g: modelled_bench_weights(selected[k], g)
                           for k, g in enumerate(self.GW)}
                self.prob.setObjective(self.objective(pts, tail, weights))
                if self._run(time_limit, gap_abs):
                    diagnostics = _solver_diagnostics(self.prob)
                else:
                    # A timeout may leave no second incumbent. The first pass was
                    # checked in full, so retain that feasible path instead of
                    # reading partial variable values as a squad.
                    for variable in self.prob.variables():
                        variable.varValue = first_incumbent[variable.name]
                    diagnostics = dict(diagnostics, used_first_pass=True)
            return self.read(src, diagnostics)
        finally:
            self._fix_first(None)

    def read(self, src, diagnostics):
        """Exact rescoring of the incumbent path under `src` projections."""
        ids, GW, x, v = self.ids, self.GW, self.x, self.valuation
        out = {'weeks': [], 'total': 0.0, 'hits': 0, 'gw': self.gw, 'horizon': self.horizon,
               'solver': diagnostics}
        available_ft = self.ft
        objective = 0.0
        squad = []
        for g in GW:
            squad = [i for i in ids if (x[(i, g)].value() or 0) > 0.5]
            evaluation = evaluate_squad([src[i] for i in squad], g, g)
            lineup = evaluation.weeks[0].lineup
            xi = [p['id'] for p in lineup.xi]
            cap = lineup.captain['id'] if lineup.captain else None
            wk = evaluation.total
            outgoing = [i for i in ids if (self.tout[(i, g)].value() or 0) > 0.5]
            # The MILP's upper bounds may leave unused FT variables below their
            # true value. Reconstruct the actual bank from the selected transfers;
            # those arbitrary slack values must never be shown as account balances.
            ledger = transfer_ledger(available_ft, len(outgoing), g, g == self.wildcard_week)
            h = ledger['hits']
            out['total'] += wk - HIT * h
            objective += v.weight(self.gw, g) * (wk - HIT * h)
            out['hits'] += h
            out['weeks'].append({
                'gw': g, 'pts': round(wk, 1), 'hits': h,
                'squad': squad, 'xi': xi, 'captain': cap,
                'autosub': round(evaluation.autosub_points, 1),
                'in': [i for i in ids if (self.tin[(i, g)].value() or 0) > 0.5],
                'out': outgoing,
                **ledger,
                'cost': sum(self.price[i] for i in squad) / 10,
            })
            available_ft = ledger['ft_next']
        # terminal value, rescored exactly
        ft_end = available_ft
        ft_value = ft_terminal_value(ft_end, v.ft_values)
        bank_end = (self.budget - sum(self.price[i] for i in squad)
                    - sum(d for i, d in self.discounts.items()
                          if i not in squad or any(i in w['out'] for w in out['weeks']))) / 10
        tail_value = 0.0
        if self.TAIL:
            tp = self.tail_source(src)
            members = [tp[i] for i in squad]
            for g in self.TAIL:
                tail_value += v.weight(self.gw, g) * evaluate_week(members, g).total
        terminal = dict(ft_end=ft_end, ft_value=round(ft_value, 3),
                        bank_end=round(bank_end, 1),
                        bank_value=round(v.bank_value * bank_end, 3),
                        tail_weeks=len(self.TAIL), tail_value=round(tail_value, 3))
        objective += ft_value + v.bank_value * bank_end + tail_value
        out['terminal'] = terminal
        out['objective'] = objective
        out['total_unrounded'] = out['total']
        out['total'] = round(out['total'], 1)
        return out


def plan(players, owned, bank, ft, gw, horizon, allow_hits=True,
         freeze_this_week=False, time_limit=30, wildcard_week=None, sell_prices=None,
         first_week_squad=None, valuation=None):
    """Optimal transfer path from `owned` over GW gw..horizon.

    `ft` is the number of free transfers available at the coming deadline
    (before Gameweek 1 pass 15: everything is free). `freeze_this_week`
    forbids any transfer before the coming deadline — solve both and the
    difference is what using the transfer now is worth versus holding it.
    Set `wildcard_week` to a gameweek inside the window to model playing the
    wildcard chip that week: unlimited free transfers at no point cost, and
    per FPL's chip rules the free-transfer bank is neither spent nor gained
    that week (it rolls over unchanged into the following deadline).
    `valuation` adds week decay and a terminal value (see Valuation); the
    default is the legacy undiscounted, zero-terminal objective.

    The result's `total` is the undiscounted in-window points net of hits
    (what the digest shows); `objective` is what decisions compare.
    """
    if first_week_squad is not None:
        if len(set(first_week_squad)) != 15 or any(i not in players for i in first_week_squad):
            raise ValueError('first_week_squad must contain 15 distinct known players')
        if freeze_this_week and set(first_week_squad) != set(owned):
            raise ValueError('first_week_squad conflicts with freeze_this_week')
    if gw > horizon:
        return None
    model = PathModel(players, owned, bank, ft, gw, horizon, allow_hits=allow_hits,
                      wildcard_week=wildcard_week, sell_prices=sell_prices,
                      valuation=valuation, extra_ids=first_week_squad or ())
    fixed = list(owned) if freeze_this_week else first_week_squad
    if freeze_this_week and any(i not in model.P for i in owned):
        return None
    return model.solve(first_week_squad=fixed, time_limit=time_limit)


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
