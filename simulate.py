"""
Monte Carlo simulation of GW1-6 for each candidate squad.

A projection gives you one number. The actual decision — "which of these squads
should I pick?" — depends on the whole distribution, and on two things a point
estimate cannot express:

  1. CORRELATION. Clean sheets are a team event. Three Arsenal defenders plus
     Raya do not fail independently; they blank together. A squad concentrated
     in a few clubs has far more variance than its projection suggests.
  2. AUTO-SUBS. A strong bench converts a non-playing starter into points. That
     is worth real points and never shows up in a projected XI total.

So the simulation runs at team level first (each club's goals scored and
conceded per gameweek), then draws player outcomes conditional on that, applies
captaincy, and finally applies FPL's auto-sub rules.

Everything is calibrated from last season's per-90 rates in the FPL API.
"""
import json, csv, argparse
import numpy as np

import project as P
from v2.squad_evaluator import apply_autosubs, captain_replacement, pick_lineup

SIMS = 40_000
V2_DATA = json.load(open('v2/projections_v2.json'))
V2_PLAYERS = {p['id']: p for p in V2_DATA['players']}
START_GW = V2_DATA.get('start_gw', 1)
HORIZON = V2_DATA.get('horizon', P.HORIZON)
WINDOW = HORIZON - START_GW + 1
POS_ID = {'GKP': 1, 'DEF': 2, 'MID': 3, 'FWD': 4}
GOAL_PTS = {1: 6, 2: 6, 3: 5, 4: 4}
CS_PTS = {1: 4, 2: 4, 3: 1, 4: 0}
XI_MIN = {1: 1, 2: 3, 3: 2, 4: 1}
XI_MAX = {1: 1, 2: 5, 3: 5, 4: 3}

rng = np.random.default_rng(20260806)

BOOT = P.BOOT
ELEM = {p['id']: p for p in BOOT['elements']}
TEAM_ID = {t['short_name']: t['id'] for t in BOOT['teams']}


# ------------------------------------------------------------------- teams
def team_match_params():
    """Per-team, per-gameweek expected goals for and against.

    Built from last season's squad-level attacking and defensive strength,
    scaled by each gameweek's fixture difficulty and home advantage.
    """
    LEAGUE_GPG = 1.45
    xg_for, xg_against = {}, {}
    for t in TEAM_ID.values():
        # defensive: minutes-weighted xGC/90 of the club's keepers and defenders
        num = den = 0.0
        for p in BOOT['elements']:
            if p['team'] == t and p['element_type'] in (1, 2) and p['minutes'] >= 900:
                num += float(p['expected_goals_conceded_per_90']) * p['minutes']
                den += p['minutes']
        xg_against[t] = (num / den) if den else 1.75      # promoted-club prior
        # attacking: sum of squad xGI per 90, scaled to a plausible goals rate
        tot = sum(float(p['expected_goal_involvements_per_90']) * p['minutes'] / 90.0
                  for p in BOOT['elements']
                  if p['team'] == t and p['minutes'] >= 450)
        xg_for[t] = tot / 38.0 if tot else 1.05

    # normalise attacking to the league average
    mean_for = np.mean([v for v in xg_for.values()])
    for t in xg_for:
        xg_for[t] = xg_for[t] / mean_for * LEAGUE_GPG

    sched = {}
    for f in P.FIX:
        gw = f.get('event')
        if gw is None or gw < START_GW or gw > HORIZON:
            continue
        for side, dkey, home in (('team_h', 'team_h_difficulty', True),
                                 ('team_a', 'team_a_difficulty', False)):
            t, d = f[side], f[dkey]
            # difficulty 2 (easy) -> score more, concede less; 5 the reverse
            att = xg_for[t] * (1.0 + (3.5 - d) * 0.13) * (1.08 if home else 0.92)
            dfn = xg_against[t] * (1.0 - (3.5 - d) * 0.13) * (0.92 if home else 1.08)
            sched.setdefault(t, {})[gw] = (max(0.25, att), max(0.25, dfn))
    return sched


def team_match_params_v2():
    """Per-team, per-gameweek expected goals from the v2 Dixon-Coles model.

    v2/season_view.json holds, for every club and gameweek, the fitted xG for
    and against each fixture. That is a far better team layer than the v1
    heuristic above -- FPL's difficulty rating correlates only -0.60 with real
    clean-sheet probability, and v2's ratings validated 1.6% behind Pinnacle's
    closing line -- so use it whenever it is present.

    Returns the same shape as team_match_params(), plus a fixture list so goals
    can be drawn once per match rather than once per side.
    """
    import os
    path = os.path.join('v2', 'season_view.json')
    if not os.path.exists(path):
        return None, None
    view = json.load(open(path))['view']
    sched, fixtures, seen = {}, [], set()
    for short, byweek in view.items():
        t = TEAM_ID.get(short)
        if t is None:
            continue
        for gw_s, matches in byweek.items():
            gw = int(gw_s)
            if gw < START_GW or gw > HORIZON:
                continue
            for m in matches:
                opp = TEAM_ID.get(m['opp'])
                if opp is None:
                    continue
                sched.setdefault(t, {})[gw] = (max(0.25, m['xg']), max(0.25, m['xgc']))
                home, away = (t, opp) if m['home'] else (opp, t)
                key = (gw, home, away)
                if key in seen:
                    continue
                seen.add(key)
                # the home side's xG is the away side's xGC; take it from the
                # home team's own row so both sides of the fixture agree
                if m['home']:
                    fixtures.append((gw, home, away, max(0.25, m['xg']), max(0.25, m['xgc'])))
                else:
                    fixtures.append((gw, home, away, max(0.25, m['xgc']), max(0.25, m['xg'])))
    return sched, fixtures


_V2_SCHED, _V2_FIXTURES = team_match_params_v2()
TEAM_SCHED = _V2_SCHED if _V2_SCHED else team_match_params()
TEAM_LAYER = 'v2 Dixon-Coles ratings' if _V2_SCHED else 'v1 FDR heuristic'

# The projection model's per-gameweek means and deadline-aware minutes, used to
# calibrate the simulation and to set a fresh lineup every gameweek.
PROJ_BY_GW = {pid: p['proj_by_gw'] for pid, p in V2_PLAYERS.items()}


def simulation_gameweeks(start_gw=START_GW, horizon=HORIZON):
    """The exact rolling gameweek window represented by simulation arrays."""
    return list(range(start_gw, horizon + 1))


def projection_window_average(values, start_gw=START_GW, horizon=HORIZON):
    """Average a full-season projection array over the active window.

    Blank gameweeks count as zero rather than disappearing from the divisor.
    """
    window = values[start_gw - 1:horizon]
    return sum(window) / max(len(window), 1)


PROJ_GW = {
    pid: projection_window_average(values)
    for pid, values in PROJ_BY_GW.items()
}


def simulate_teams(sims):
    """Draw goals for/against for every club in every gameweek.

    Returned arrays are shared across all squads in a run, so two squads holding
    the same club's defenders correctly rise and fall together.
    """
    gf = {}
    ga = {}
    for t in TEAM_SCHED:
        gf[t] = np.zeros((sims, WINDOW), dtype=np.int16)
        ga[t] = np.zeros((sims, WINDOW), dtype=np.int16)
    if _V2_FIXTURES:
        # One draw per match, so the home side's goals ARE the away side's goals
        # conceded. Without this a squad holding both sides of a fixture (say
        # City's defence and a Bournemouth midfielder in GW1) sees them succeed
        # independently, which is impossible.
        for gw, home, away, xg_h, xg_a in _V2_FIXTURES:
            index = gw - START_GW
            h = rng.poisson(xg_h, sims)
            a = rng.poisson(xg_a, sims)
            gf[home][:, index] += h; ga[home][:, index] += a
            gf[away][:, index] += a; ga[away][:, index] += h
        return gf, ga
    for t, byweek in TEAM_SCHED.items():
        for gw in range(START_GW, HORIZON + 1):
            if gw not in byweek:
                continue
            att, dfn = byweek[gw]
            index = gw - START_GW
            gf[t][:, index] = rng.poisson(att, sims)
            ga[t][:, index] = rng.poisson(dfn, sims)
    return gf, ga


# ----------------------------------------------------------------- players
def player_params(pid):
    """Per-90 rates and start probability for one player."""
    p = ELEM[pid]
    projection = V2_PLAYERS[pid]
    et = p['element_type']
    per90 = max(p['minutes'] / 90.0, 1e-9)

    # Historical per-start minutes remain the fallback outside a live deadline
    # override. The actual start/cameo mixture comes from player_model v2.
    if p['starts'] >= 5 and p['minutes'] >= 450:
        mps = min(92.0, p['minutes'] / p['starts'])
    else:
        mps = 80.0
    all_starts = projection.get('start_by_gw') or [
        projection.get('start_rate', 0.0)
    ] * HORIZON
    all_plays = projection.get('play_by_gw') or [
        s + (1.0 - s) * 0.20 for s in all_starts
    ]
    all_metadata = projection.get('availability_by_gw') or []
    starts = [all_starts[gw - 1] for gw in range(START_GW, HORIZON + 1)]
    plays = [all_plays[gw - 1] for gw in range(START_GW, HORIZON + 1)]
    start_minutes, cameo_minutes, cameo_rates = [], [], []
    for index, gw in enumerate(range(START_GW, HORIZON + 1)):
        row = all_metadata[gw - 1] if gw - 1 < len(all_metadata) and all_metadata[gw - 1] else {}
        sm = float(row.get('start_minutes', mps))
        cm = float(row.get('cameo_minutes', 25.0))
        ps = float(starts[index])
        pp = float(plays[index])
        pc = ((pp - ps) / max(1.0 - ps, 1e-9)) if ps < 1.0 else 0.0
        start_minutes.append(sm)
        cameo_minutes.append(cm)
        cameo_rates.append(float(np.clip(pc, 0.0, 1.0)))

    if p['minutes'] >= 450:
        xg90 = float(p['expected_goals_per_90'])
        xa90 = float(p['expected_assists_per_90'])
        bonus90 = p['bonus'] / per90
        saves90 = float(p['saves_per_90'])
        yellow90 = p['yellow_cards'] / per90
    else:
        # no meaningful history: back off to a modest position-typical rate
        base = {1: 0.0, 2: 0.05, 3: 0.13, 4: 0.28}[et]
        xg90, xa90 = base, base * 0.6
        bonus90, saves90, yellow90 = 0.25, (2.6 if et == 1 else 0.0), 0.15

    par = dict(pid=pid, et=et, team=p['team'],
               p_start_by_gw=starts, p_cameo_by_gw=cameo_rates,
               start_minutes_by_gw=start_minutes,
               cameo_minutes_by_gw=cameo_minutes,
               xg90=xg90, xa90=xa90, bonus90=bonus90, saves90=saves90,
               yellow90=yellow90,
               dc90=p['defensive_contribution_per_90'],
               price=p['now_cost'] / 10, name=p['web_name'])
    par['k_att'], par['add'] = _attacking_calibration(par)
    return par


def _attacking_calibration(par):
    """Scale attacking rates so the simulation's mean matches the projection.

    Raw xG and xA understate realised returns -- finishers outperform their xG,
    and the bonus model is deliberately crude. Left uncorrected the simulation
    runs ~20-30% light on attackers and roughly on-target for defenders, which
    would bias the whole comparison towards defensive squads.

    The projection model is already anchored on last season's actual points, so
    it is the better estimate of the MEAN. The simulation's job is the SHAPE --
    correlation, variance, tails. So we solve for the multiplier on the lumpy
    attacking component that reconciles the two, and leave everything else alone.
    """
    et, t = par['et'], par['team']
    target = PROJ_GW.get(par['pid'])
    if not target or t not in TEAM_SCHED:
        return 1.0, 0.0

    active = [(gw - START_GW, TEAM_SCHED[t][gw])
              for gw in range(START_GW, HORIZON + 1)
              if gw in TEAM_SCHED[t]]
    weeks = [fixture for _, fixture in active]
    if not weeks:
        return 1.0, 0.0
    p_start = sum(par['p_start_by_gw'][i] for i, _ in active) / WINDOW
    p_cameo = sum(
        (1 - par['p_start_by_gw'][i]) * par['p_cameo_by_gw'][i]
        for i, _ in active
    ) / WINDOW
    p_played = p_start + p_cameo
    minute_share = sum(
        par['p_start_by_gw'][i] * par['start_minutes_by_gw'][i] / 90.0
        + (1 - par['p_start_by_gw'][i]) * par['p_cameo_by_gw'][i]
        * par['cameo_minutes_by_gw'][i] / 90.0
        for i, _ in active
    ) / WINDOW

    steady = p_start * 2.0 + p_cameo
    if et in (1, 2, 3):
        steady += CS_PTS[et] * sum(
            float(np.exp(-fixture[1])) * par['p_start_by_gw'][i]
            for i, fixture in active
        ) / WINDOW
    if et in (1, 2):
        steady -= sum(
            fixture[1] / 2.0 * par['p_start_by_gw'][i]
            for i, fixture in active
        ) / WINDOW
    if et == 1:
        steady += par['saves90'] * minute_share / 3.0
    if et in (2, 3, 4) and par['dc90'] > 0:
        thr = 10 if et == 2 else 12
        steady += 2.0 * P._poisson_at_least(
            par['dc90'] * minute_share / max(p_played, 1e-9), thr
        ) * p_played
    steady -= min(0.6, par['yellow90'] * minute_share)

    attacking = (par['xg90'] * GOAL_PTS[et]
                 + par['xa90'] * 3.0) * minute_share
    attacking += min(0.9, par['bonus90'] * 0.55) * minute_share

    # Scale the attacking component as far as is sensible...
    k = 1.0 if attacking <= 1e-6 else float(
        np.clip((target - steady) / attacking, 0.25, 4.0))

    # ...then close whatever gap is left with a flat per-match term.
    #
    # k alone cannot do the job. It only multiplies goals, assists and bonus, so
    # a player with almost no attacking component has almost nothing to scale:
    # 16 of 28 goalkeepers pinned k at the 4.0 ceiling and the simulator still
    # came out 0.32 points a gameweek light on them, against 0.20 for midfielders
    # and forwards and 0.00 for defenders. A bias that size and that uneven across
    # positions is enough to reorder squads on its own.
    add = float(target - (steady + attacking * k))
    return k, add


def simulate_player(par, gf, ga, sims):
    """Points for one player across all sims and gameweeks."""
    et, t = par['et'], par['team']
    pts = np.zeros((sims, WINDOW), dtype=np.float32)
    if t not in gf:
        return pts

    for gw in range(WINDOW):
        actual_gw = START_GW + gw
        if actual_gw not in TEAM_SCHED.get(t, {}):
            par.setdefault('_played', []).append(np.zeros(sims, dtype=bool))
            continue
        p_start = par['p_start_by_gw'][gw]
        p_cameo = par['p_cameo_by_gw'][gw]
        started = rng.random(sims) < p_start
        # a non-starter sometimes appears off the bench
        cameo = (~started) & (rng.random(sims) < p_cameo)
        played = started | cameo
        frac = np.where(started, par['start_minutes_by_gw'][gw] / 90.0,
                        par['cameo_minutes_by_gw'][gw] / 90.0)

        # appearance points: 1 for any minutes, 2 for 60+
        pts[:, gw] += np.where(started, 2.0, 0.0) + np.where(cameo, 1.0, 0.0)

        # attacking returns scale with time on the pitch, and with how many
        # goals the team actually scored this week (shared upside)
        team_goals = gf[t][:, gw]
        boost = np.where(team_goals > 0, 1.0, 0.0) * (team_goals / max(
            np.mean(gf[t][:, gw]), 0.3))
        k = par.get('k_att', 1.0)
        g = rng.poisson(np.clip(par['xg90'] * k * frac * boost, 0, 6) * played)
        a = rng.poisson(np.clip(par['xa90'] * k * frac * boost, 0, 6) * played)
        pts[:, gw] += g * GOAL_PTS[et] + a * 3.0

        # clean sheets: a TEAM event, so every defender and the keeper share it
        cs = (ga[t][:, gw] == 0) & started
        if et in (1, 2, 3):
            pts[:, gw] += cs * CS_PTS[et]
        # goals-conceded penalty for keepers and defenders: -1 per 2 conceded
        if et in (1, 2):
            pts[:, gw] -= np.floor(ga[t][:, gw] / 2.0) * started
        if et == 1:
            saves = rng.poisson(np.clip(par['saves90'] * frac, 0, 12) * started)
            pts[:, gw] += np.floor(saves / 3.0)

        # Defensive contribution. Drawn from a negative binomial rather than a
        # Poisson: defensive action counts are over-dispersed, because how much
        # defending a player does depends on game state, opponent and how much
        # of the ball his side has. Poisson understates the upper tail, which is
        # what decides whether a player below the threshold ever clears it.
        if et in (2, 3, 4) and par['dc90'] > 0:
            thr = 10 if et == 2 else 12
            # `frac` varies per simulation (starters play longer than cameos),
            # so the mean is a vector and the NB parameters broadcast with it.
            m = np.clip(par['dc90'] * frac, 0.01, 30)
            r = 12.0
            draws = rng.negative_binomial(r, r / (r + m))
            pts[:, gw] += (draws >= thr) * played * 2.0

        # Bonus. Triggered by having a good game, which is position-specific:
        # attackers earn it from goals and assists, keepers and defenders from
        # clean sheets and saves. Gating it on (goals + assists) alone -- as this
        # did originally -- awarded goalkeepers no bonus at all, since they
        # essentially never score, which is a large slice of a keeper's real
        # scoring quietly deleted.
        good = (g + a) > 0
        if et in (1, 2):
            good = good | cs
        bp = rng.random(sims) < np.clip(par['bonus90'] * k * frac * 0.55, 0, 0.9)
        pts[:, gw] += (good & bp) * rng.integers(1, 4, sims)

        # cards
        pts[:, gw] -= (rng.random(sims) < np.clip(par['yellow90'] * frac, 0, 0.6)) * played

        # residual calibration, applied only when he actually plays
        pts[:, gw] += par.get('add', 0.0) * played

        # remember whether he played, for auto-subs
        par.setdefault('_played', []).append(played)
    return pts


# ------------------------------------------------------------------ squads
_ADD_CACHE = {}
PILOT_SIMS = 1500


def calibrated_params(pid, gf, ga):
    """Player parameters with the residual term fitted by simulation, not algebra.

    The analytic estimate of the "steady" component never quite matches what the
    simulation produces — floor() on saves and goals conceded, Jensen gaps on the
    clean-sheet term, position-specific bonus rules. Rather than chase each of
    those, run a short pilot, measure the shortfall directly, and set the
    residual from that. It is slower but it cannot be quietly wrong.
    """
    if pid in _ADD_CACHE:
        par = player_params(pid)
        par['add'] = _ADD_CACHE[pid]
        par['_played'] = []
        return par
    par = player_params(pid)
    target = PROJ_GW.get(pid)
    if target:
        # The pilot runs at fewer simulations than the main pass, so the team
        # goal arrays have to be sliced to match or the shapes will not broadcast.
        n = min(PILOT_SIMS, next(iter(gf.values())).shape[0])
        key = ('_slice', n)
        if key not in _ADD_CACHE:
            _ADD_CACHE[key] = ({t: v[:n] for t, v in gf.items()},
                               {t: v[:n] for t, v in ga.items()})
        gf_p, ga_p = _ADD_CACHE[key]
        probe = dict(par, add=0.0, _played=[])
        pts = simulate_player(probe, gf_p, ga_p, n)
        got = pts.mean(axis=0).sum() / WINDOW
        # spread the per-gameweek shortfall over the matches he actually plays
        p_play = sum(
            par['p_start_by_gw'][index]
            + (1 - par['p_start_by_gw'][index]) * par['p_cameo_by_gw'][index]
            for index, gw in enumerate(range(START_GW, HORIZON + 1))
            if gw in TEAM_SCHED.get(par['team'], {})
        ) / WINDOW
        par['add'] = float((target - got) / max(p_play, 0.15))
    else:
        par['add'] = 0.0
    _ADD_CACHE[pid] = par['add']
    par['_played'] = []
    return par


_OUTCOME_CACHE = {}


def player_outcome(pid, gf, ga, sims):
    """One paired outcome for a player, reused by every compared squad."""
    key = (pid, sims, id(gf))
    if key in _OUTCOME_CACHE:
        return _OUTCOME_CACHE[key]
    par = calibrated_params(pid, gf, ga)
    par['_played'] = []
    pts = simulate_player(par, gf, ga, sims)
    played = np.stack(par['_played'], axis=1)
    _OUTCOME_CACHE[key] = (par, pts, played)
    return _OUTCOME_CACHE[key]


def simulate_squad(pids, gf, ga, sims, label):
    outcomes = [player_outcome(i, gf, ga, sims) for i in pids]
    pars = [outcome[0] for outcome in outcomes]
    pts = np.stack([outcome[1] for outcome in outcomes])       # (15, sims, GW)
    played = np.stack([outcome[2] for outcome in outcomes])    # (15, sims, GW)
    index = {pid: i for i, pid in enumerate(pids)}
    squad = [V2_PLAYERS[pid] for pid in pids]

    total = np.zeros((sims, WINDOW), dtype=np.float32)
    subbed = np.zeros((sims, WINDOW), dtype=np.float32)
    cap_extra = np.zeros((sims, WINDOW), dtype=np.float32)
    top_club = 0

    # Managers reset XI, bench order, captain and vice every gameweek. Use the
    # same shared rule engine as weekly.py rather than freezing one six-week XI.
    from collections import Counter
    for gw_index, gw in enumerate(range(START_GW, HORIZON + 1)):
        lineup = pick_lineup(squad, gw)
        xi_ids = [p['id'] for p in lineup.xi]
        for pid in xi_ids:
            total[:, gw_index] += pts[index[pid], :, gw_index]

        clubs = Counter(ELEM[pid]['team'] for pid in xi_ids)
        top_club = max(top_club, max(clubs.values()) if clubs else 0)

        captain_id = lineup.captain['id'] if lineup.captain else None
        vice_id = lineup.vice['id'] if lineup.vice else None
        for simulation in range(sims):
            appeared = {
                pid: bool(played[index[pid], simulation, gw_index]) for pid in pids
            }
            subs = apply_autosubs(lineup.xi, lineup.bench, appeared)
            for _, substitute_id in subs.substitutions:
                value = pts[index[substitute_id], simulation, gw_index]
                subbed[simulation, gw_index] += value

            doubled = captain_replacement(captain_id, vice_id, appeared)
            if doubled is not None:
                cap_extra[simulation, gw_index] = pts[
                    index[doubled], simulation, gw_index
                ]

    total += subbed + cap_extra

    season = total.sum(axis=1)

    # how much of the total comes from the armband, and the worst gameweek
    weekly = total
    return {
        'label': label,
        'mean': float(season.mean()),
        'median': float(np.median(season)),
        'p10': float(np.percentile(season, 10)),
        'p25': float(np.percentile(season, 25)),
        'p75': float(np.percentile(season, 75)),
        'p90': float(np.percentile(season, 90)),
        'sd': float(season.std()),
        'season': season,
        'captain_pts': float(cap_extra.sum(axis=1).mean()),
        'autosub_pts': float(subbed.sum(axis=1).mean()),
        'top_club_xi': top_club,
        'worst_gw': float(weekly.min(axis=1).mean()),
        'best_gw': float(weekly.max(axis=1).mean()),
        'names': [p['name'] for p in pars],
    }


def template_squad(players):
    """The most-owned squad that is actually LEGAL -- a proxy for 'the field'.

    Greedily taking the 15 most-owned players gives a £111.5m squad, which is not
    a team anyone can own and would flatter it badly in a comparison. So this
    maximises total ownership subject to the real constraints: £100.0m, 2/5/5/3
    and max 3 per club.
    """
    import pulp
    prob = pulp.LpProblem('template', pulp.LpMaximize)
    x = {r['id']: pulp.LpVariable(f"t{r['id']}", cat='Binary') for r in players}
    prob += pulp.lpSum(x[r['id']] * r['sel'] for r in players)
    for et, n in {1: 2, 2: 5, 3: 5, 4: 3}.items():
        prob += pulp.lpSum(x[r['id']] for r in players if r['et'] == et) == n
    prob += pulp.lpSum(x[r['id']] * r['price_t'] for r in players) <= 1000
    for club in {r['team'] for r in players}:
        prob += pulp.lpSum(x[r['id']] for r in players if r['team'] == club) <= 3
    prob.solve(pulp.HiGHS(msg=False))
    return [r['id'] for r in players if x[r['id']].value() > 0.5]


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sims', type=int, default=SIMS)
    args = ap.parse_args()
    sims = args.sims

    rows = list(csv.DictReader(open('data/projections.csv')))
    meta = [{'id': int(r['id']), 'et': POS_ID[r['pos']], 'team': TEAM_ID[r['team']],
             'sel': float(r['sel_pct']),
             'price_t': int(round(float(r['price']) * 10))} for r in rows]

    squads = json.load(open('data/squads.json'))
    gf, ga = simulate_teams(sims)

    results = []
    for s in squads:
        pids = [int(p['id']) for p in s['squad']]
        lbl = s['label'].split(' - ')[0].replace('OPTION ', 'Option ')
        nick = s['label'].split('"')[1] if '"' in s['label'] else ''
        results.append(simulate_squad(pids, gf, ga, sims, f'{lbl} ({nick})'))

    tmpl = template_squad(meta)
    results.append(simulate_squad(tmpl, gf, ga, sims, 'The template (most-owned)'))

    print(f'\n{sims:,} simulations of GW{START_GW}-{HORIZON}, '
          f'including captaincy, auto-subs and team-level clean-sheet correlation')
    print(f'team layer: {TEAM_LAYER}; player means calibrated to data/projections.csv\n')
    print(f"{'squad':<34}{'mean':>7}{'p10':>7}{'p90':>7}{'sd':>7}"
          f"{'capt':>7}{'subs':>7}{'worstGW':>9}{'bestGW':>8}{'maxclub':>8}")
    print('-' * 102)
    for r in sorted(results, key=lambda x: -x['mean']):
        print(f"{r['label']:<34}{r['mean']:>7.0f}{r['p10']:>7.0f}"
              f"{r['p90']:>7.0f}{r['sd']:>7.1f}{r['captain_pts']:>7.0f}"
              f"{r['autosub_pts']:>7.1f}{r['worst_gw']:>9.0f}{r['best_gw']:>8.0f}"
              f"{r['top_club_xi']:>8}")

    print('\n--- head to head: P(row beats column) ---')
    hdr = ''.join(f"{r['label'].split(' ')[1][:6]:>9}" for r in results)
    print(f"{'':<20}{hdr}")
    for a in results:
        line = f"{a['label'][:19]:<20}"
        for b in results:
            if a is b:
                line += f"{'—':>9}"
            else:
                line += f"{100 * np.mean(a['season'] > b['season']):>8.0f}%"
        print(line)

    print('\n--- vs the template (this is what rank actually tracks) ---')
    t = [r for r in results if 'template' in r['label']][0]
    for r in results:
        if r is t:
            continue
        d = r['season'] - t['season']
        print(f"  {r['label']:<34} beats template {100*np.mean(d>0):>4.0f}% of the time, "
              f"mean {d.mean():>+6.1f} pts, "
              f"P(+25 or better) {100*np.mean(d>25):>4.0f}%, "
              f"P(-25 or worse) {100*np.mean(d<-25):>4.0f}%")
