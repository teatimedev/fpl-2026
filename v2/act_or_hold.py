"""
Act or hold: this week's transfer decision under forecast uncertainty.

Replaces the old asymmetric "gain must beat holding by 2.0 points per move"
buffer with a sample-average-approximation (SAA) of the two-stage problem

    choose this week's squad a (non-anticipative: one choice for all worlds)
    then, in each plausible world s, re-plan every later week optimally

    a* = argmax_a  mean_s  V_s(a)

where V_s(a) is the planner's objective (decayed points, hits, terminal
value) with this week's squad fixed to a and every later week re-planned
from the forecast of sample s, and "hold" is always one of the a, evaluated
on the SAME samples (common random numbers). This is the open-source FPL
solver community's "sensitivity analysis" (re-solve under perturbed
projections, record this week's move each time), made into a proper
comparison: each candidate first week, and hold, is scored on the same draws.

What a sample is. Not "the truth": the forecast the manager will have at the
NEXT deadline. Later weeks are re-planned with it and scored with it, which
is unbiased when forecasts are martingales (E[truth | next forecast] = next
forecast). The coming gameweek is not perturbed: it is picked on today's
information. So the spread to use is the one-week forecast REVISION, which
the 2026/27 archive measures directly (consecutive deadline forecasts of the
same target gameweek, players projected >= 2 pts, GW2->3 ... GW5->6):
  player-level relative revision sd 0.24, 0.20, 0.16, 0.14 (falling as the
  season's evidence accumulates; heavy left tail — 0-4.5% of players lose
  over half their projection in a week, i.e. news), and a within-player
  week-to-week component of about 0.11.
Each sample therefore multiplies a player's later-week projections by
    1 + z_player + WEEK_SD * e_player,week        (clipped at zero)
with z_player ~ N(0, 0.15) and, with probability 0.02, a news drop
U(-100%, -50%) instead. A player with no route to minutes stays at zero.
Appearance probabilities are not perturbed. Revisions include model code
changes between deadlines, so the early weeks overstate information.

Why this prices holding without a buffer: a fixed path is linear in the
forecast, so mean-one noise leaves its value unchanged, but re-planning is
a maximum over paths and gains from noise (Jensen). A banked transfer is
flexibility to act on next week's news, so holding earns its option value
here — and loses it at the five-transfer cap, where holding wastes one.

Choosing. Among the tested actions the one-standard-error rule (Breiman et
al. 1984; Hastie, Tibshirani & Friedman 2009 §7.10) is applied to the
Monte Carlo estimate: take the best mean, then prefer the action with the
fewest transfers whose mean is within one paired standard error of it. This
guards against acting on sampling noise; it is not a points buffer and it
shrinks as samples grow.

Honest limits: later weeks after the next deadline learn nothing more (for
every action alike); prices are static; the noise model is fitted to four
weekly revisions; the smaller sampling pool (80 players) and absolute MIP gap
(0.1) add noise shared by all actions; and no realised-points validation of
the policy is possible yet.
"""
import hashlib
import math
import random
import time

from planner import PathModel

PERSISTENT_SD = 0.15
WEEK_SD = 0.10
JUMP_P = 0.02
SAMPLES = 24
DISCOVERY = 6
MAX_ACTIONS = 3          # tested non-hold actions, besides hold
SOLVE_LIMIT = 8          # seconds per sample solve
GAP_ABS = 0.1            # points; small next to the paired sd of ~1.5-2.5
BUDGET = 240             # seconds for the sampler; checked between worlds
MIN_SAMPLES = 10
SE_RULE = 1.0


def seed_for(*parts):
    digest = hashlib.sha256('|'.join(str(p) for p in parts).encode()).hexdigest()
    return int(digest[:12], 16)


def player_factor(rng, persistent_sd=PERSISTENT_SD, jump_p=JUMP_P):
    """A player's relative forecast revision by the next deadline."""
    if rng.random() < jump_p:
        return rng.uniform(-1.0, -0.5)          # news: injury, dropped, sold
    return rng.gauss(0, persistent_sd)


def perturb(model, rng, persistent_sd=PERSISTENT_SD, week_sd=WEEK_SD, jump_p=JUMP_P):
    """One plausible next-deadline forecast: perturbed copies of pool players.

    The coming gameweek is not perturbed: its lineup is picked with today's
    information and is scored in expectation either way.
    """
    world = {}
    base_tail = model.tail_source()
    for i in model.ids:
        p = model.P[i]
        z = player_factor(rng, persistent_sd, jump_p)
        v = list(p['proj_by_gw'])
        for g in model.GW[1:]:
            if g - 1 < len(v):
                v[g - 1] = v[g - 1] * max(0.0, 1 + z + week_sd * rng.gauss(0, 1))
        q = {**p, 'proj_by_gw': v}
        if model.TAIL:
            t = base_tail[i]
            f = max(0.0, 1 + z + week_sd * rng.gauss(0, 1))
            q['_tail'] = {**t, 'proj_by_gw': [x * f for x in t['proj_by_gw']]}
        world[i] = q
    return world


def _paired(model, outgoing, incoming):
    """Outgoing and incoming ids in matching order, paired by position."""
    outs, ins = [], []
    for pos in ('GKP', 'DEF', 'MID', 'FWD'):
        o = sorted(i for i in outgoing if model.P[i]['pos'] == pos)
        n = sorted(i for i in incoming if model.P[i]['pos'] == pos)
        outs += o
        ins += n
    return outs, ins


def _key(squad):
    return frozenset(squad)


def _stats(values):
    n = len(values)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else 0.0
    return mean, sd, sd / math.sqrt(n) if n else 0.0


def decide(model, owned, candidates, nominal=None, samples=SAMPLES, discovery=DISCOVERY,
           max_actions=MAX_ACTIONS, seed=0, budget=BUDGET, solve_limit=SOLVE_LIMIT,
           bench_weights=None, persistent_sd=PERSISTENT_SD, week_sd=WEEK_SD,
           se_rule=SE_RULE, clock=time.monotonic):
    """Compare hold with candidate first-week squads across sampled worlds.

    `candidates` is [(label, first_week_squad)]; `nominal` maps a squad key to
    its nominal (unperturbed) objective where known. Returns a dict with the
    chosen action, its expected gain over holding and how often it won.
    """
    start = clock()
    rng = random.Random(seed)
    hold_key = _key(owned)
    actions = {hold_key: dict(label='hold', squad=list(owned))}
    for label, squad in candidates:
        k = _key(squad)
        if k not in actions and len(k) == 15 and all(i in model.P for i in squad):
            actions[k] = dict(label=label, squad=list(squad))

    def solve(squad, world):
        res = model.solve(first_week_squad=squad, players=world, time_limit=solve_limit,
                          refit=False, bench_weights=bench_weights, gap_abs=GAP_ABS)
        return res

    # ---- discovery: what the unconstrained planner does in other worlds
    found = {}
    for _ in range(discovery):
        if clock() - start > budget / 3:
            break
        world = perturb(model, rng, persistent_sd, week_sd)
        res = solve(None, world)
        if res:
            k = _key(res['weeks'][0]['squad'])
            found[k] = found.get(k, 0) + 1
            if k not in actions:
                actions[k] = dict(label='sampled planner', squad=res['weeks'][0]['squad'])
    # Keep hold, the given candidates ranked by nominal value, and anything
    # the sampled planner chose at least twice.
    ranked = [k for k in actions if k != hold_key]
    ranked.sort(key=lambda k: (-(found.get(k, 0) >= 2),
                               -(nominal or {}).get(k, -1e9), -found.get(k, 0)))
    keep = [hold_key] + ranked[:max_actions]

    # ---- evaluation on common worlds
    values = {k: [] for k in keep}
    used = 0
    for s in range(samples):
        if used >= MIN_SAMPLES and clock() - start > budget:
            break
        world = perturb(model, rng, persistent_sd, week_sd)
        row = {}
        for k in keep:
            res = solve(actions[k]['squad'], world)
            if res is None:
                row = None
                break
            row[k] = res['objective']
        if row is None:
            continue
        for k in keep:
            values[k].append(row[k])
        used += 1
    if used < 2:
        return None

    hold_vals = values[hold_key]
    best_per_sample = [max(keep, key=lambda k: values[k][s]) for s in range(used)]
    rows = []
    for k in keep:
        diffs = [a - b for a, b in zip(values[k], hold_vals)]
        mean, sd, se = _stats(diffs)
        squad = actions[k]['squad']
        outs, ins = _paired(model, [i for i in owned if i not in k],
                            [i for i in squad if i not in hold_key])
        rows.append(dict(key=k, label=actions[k]['label'], squad=squad,
                         in_=ins, out=outs,
                         gain=mean, sd=sd, se=se,
                         p_beats_hold=(sum(d > 1e-6 for d in diffs) / used) if k != hold_key else None,
                         p_best=sum(b == k for b in best_per_sample) / used,
                         found=found.get(k, 0),
                         nominal_gain=((nominal or {}).get(k) - (nominal or {}).get(hold_key))
                         if nominal and k in nominal and hold_key in nominal else None))
    best = max(rows, key=lambda r: r['gain'])
    # One-standard-error rule on the paired difference to the best action.
    close = []
    for r in rows:
        d = [a - b for a, b in zip(values[best['key']], values[r['key']])]
        mean, _, se = _stats(d)
        if mean <= se_rule * se + 1e-9:
            close.append(r)
    chosen = min(close, key=lambda r: (len(r['in_']), -r['gain']))
    for r in rows:
        r['chosen'] = r is chosen
    return dict(chosen=chosen, rows=rows, samples=used, discovery=sum(found.values()),
                seed=seed, runtime_s=round(clock() - start, 1),
                noise=dict(persistent_sd=persistent_sd, week_sd=week_sd, jump_p=JUMP_P,
                           basis='one-week forecast revision, 2026/27 GW2-6'),
                rule=f'best mean objective; fewest moves within {se_rule:g} paired standard error')


def public_rows(result):
    """JSON-safe action rows for weekly.json."""
    out = []
    for r in result['rows']:
        out.append(dict(source=r['label'], status='scored', in_=r['in_'], out=r['out'],
                        n_now=len(r['in_']), gain=round(r['gain'], 2), se=round(r['se'], 2),
                        p_beats_hold=(round(r['p_beats_hold'], 3)
                                      if r['p_beats_hold'] is not None else None),
                        p_best=round(r['p_best'], 3),
                        nominal_gain=(round(r['nominal_gain'], 2)
                                      if r['nominal_gain'] is not None else None),
                        qualifies=r['chosen'], hold=not r['in_']))
    return out


def build_model(players, owned, bank, ft, gw, horizon, sell_prices, valuation,
                candidate_squads=(), pool_size=80):
    """A smaller pool than the nominal planner's: sampling needs many solves.
    The restriction applies to every action alike."""
    extra = set()
    for squad in candidate_squads:
        extra |= set(squad)
    return PathModel(players, owned, bank, ft, gw, horizon, sell_prices=sell_prices,
                     valuation=valuation, pool_size=pool_size, extra_ids=extra)


def run(players, owned, bank, ft, gw, horizon, sell_prices, valuation, candidates,
        seed=0, pool_size=80, **kwargs):
    """Nominal continuation for every candidate, then the sampled decision.

    Returns (result or None, nominal): `nominal` maps each candidate squad key
    to its unperturbed objective on the same model, so every candidate —
    sampled or not — has an act-versus-wait value on one basis.
    """
    model = build_model(players, owned, bank, ft, gw, horizon, sell_prices, valuation,
                        [s for _, s in candidates], pool_size=pool_size)
    nominal = {}
    hold = model.solve(first_week_squad=list(owned), time_limit=SOLVE_LIMIT * 2, refit=False,
                       gap_abs=GAP_ABS)
    if hold is None:
        return None, nominal
    nominal[_key(owned)] = hold['objective']
    usable = []
    for label, squad in candidates:
        k = _key(squad)
        if len(k) != 15 or any(i not in model.P for i in squad):
            continue
        if k not in nominal:
            res = model.solve(first_week_squad=list(squad), time_limit=SOLVE_LIMIT * 2,
                              refit=False, gap_abs=GAP_ABS)
            if res is None:
                continue
            nominal[k] = res['objective']
        usable.append((label, squad))
    result = decide(model, owned, usable, nominal=nominal, seed=seed, **kwargs)
    return result, nominal


def nominal_gain(nominal, owned, squad):
    """Nominal act-versus-wait objective gain of one first-week squad, or None."""
    k, h = _key(squad), _key(owned)
    if k in nominal and h in nominal:
        return nominal[k] - nominal[h]
    return None
